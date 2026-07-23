"""Qdrant implementation of VectorStore with payload-level access filtering."""
from __future__ import annotations

import uuid

from ..config import Settings
from .store import CONTENT, AccessFilter, Chunk, Hit, VectorStore

# Deterministic namespace so re-indexing the same chunk id yields the same point
# id (idempotent upserts). Qdrant point ids must be uint64 or a UUID string.
_ID_NAMESPACE = uuid.UUID("6f1a4b7e-0c2d-4c1e-9a3b-2f0e6d5c4b3a")


def _point_id(raw: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, raw))


class QdrantStore(VectorStore):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._collection = settings.qdrant.collection

    def _get_client(self):
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(url=self._settings.qdrant.url)
        return self._client

    async def _ensure_collection(self, vector_size: int) -> None:
        """Create the collection on first write (cosine distance)."""
        from qdrant_client import models as qm

        client = self._get_client()
        if not await client.collection_exists(self._collection):
            await client.create_collection(
                collection_name=self._collection,
                vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
            )

    def _access_filter(self, access: AccessFilter):
        """Translate the profile-derived access into a Qdrant payload filter.

        A chunk is visible if it is public (no department) OR it matches the
        user's department, AND — when the chunk restricts roles — the user holds
        one of them. Enforced server-side, before anything reaches the LLM.
        """
        from qdrant_client import models as qm

        dept_ok = qm.Filter(
            should=[
                qm.IsNullCondition(is_null=qm.PayloadField(key="department")),
                qm.FieldCondition(key="department", match=qm.MatchValue(value=access.department or "")),
            ]
        )
        conditions = [dept_ok]
        if access.roles:
            conditions.append(
                qm.Filter(
                    should=[
                        qm.IsEmptyCondition(is_empty=qm.PayloadField(key="roles")),
                        qm.FieldCondition(key="roles", match=qm.MatchAny(any=access.roles)),
                    ]
                )
            )
        return qm.Filter(must=conditions)

    @staticmethod
    def _kind_condition(kind: str):
        """Keep knowledge and commands out of each other's results.

        Points written before ``kind`` existed carry no such field at all, so
        asking for content accepts «missing» as content — otherwise the first
        deploy of this code would silently empty every existing index.
        """
        from qdrant_client import models as qm

        match = qm.FieldCondition(key="kind", match=qm.MatchValue(value=kind))
        if kind != CONTENT:
            return match
        return qm.Filter(
            should=[qm.IsEmptyCondition(is_empty=qm.PayloadField(key="kind")), match]
        )

    async def search(
        self,
        query_vector: list[float],
        access: AccessFilter,
        limit: int = 5,
        kind: str = CONTENT,
    ) -> list[Hit]:
        from qdrant_client import models as qm

        client = self._get_client()
        # Nothing indexed yet -> no hits (avoids a 404 on a fresh instance).
        if not await client.collection_exists(self._collection):
            return []
        query_filter = qm.Filter(
            must=[self._access_filter(access), self._kind_condition(kind)]
        )
        result = await client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        hits: list[Hit] = []
        for point in result.points:
            payload = point.payload or {}
            hits.append(
                Hit(
                    text=payload.get("text", ""),
                    kind=payload.get("kind") or CONTENT,
                    source=payload.get("source"),
                    score=point.score,
                    is_qa=payload.get("is_qa", False),
                    video_url=payload.get("video_url"),
                    links=payload.get("links") or [],
                )
            )
        # Curated Q&A first, then by score.
        hits.sort(key=lambda h: (not h.is_qa, -h.score))
        return hits

    async def reset(self) -> None:
        """Drop the collection so a full reindex can rebuild it from scratch."""
        client = self._get_client()
        if await client.collection_exists(self._collection):
            await client.delete_collection(self._collection)

    async def delete_kind(self, kind: str) -> None:
        """Drop every point of one kind, leaving the others alone.

        A command deleted from actions.yaml has to stop being offered, and an
        idempotent upsert cannot do that — nothing rewrites a point nobody
        mentions any more. So the actions index is replaced, not patched.
        """
        from qdrant_client import models as qm

        client = self._get_client()
        if not await client.collection_exists(self._collection):
            return
        await client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[self._kind_condition(kind)])
            ),
        )

    async def payload_sample(self, kind: str) -> dict | None:
        """One stored payload of this kind, or ``None`` if there are none.

        One cheap call that answers «is anything of this kind indexed, and which
        version of it» — used at startup to notice that actions.yaml changed.
        """
        from qdrant_client import models as qm

        client = self._get_client()
        if not await client.collection_exists(self._collection):
            return None
        points, _ = await client.scroll(
            collection_name=self._collection,
            scroll_filter=qm.Filter(must=[self._kind_condition(kind)]),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        return (points[0].payload or {}) if points else None

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        from qdrant_client import models as qm

        if not vectors:
            return
        await self._ensure_collection(len(vectors[0]))
        points = [
            qm.PointStruct(
                id=_point_id(chunk.id),
                vector=vector,
                payload={
                    **(chunk.meta or {}),   # extras first: they never win
                    "text": chunk.text,
                    "kind": chunk.kind,
                    "source": chunk.source,
                    "is_qa": chunk.is_qa,
                    "department": chunk.department,
                    "roles": chunk.roles,
                    "collection": chunk.collection,
                    "video_url": chunk.video_url,
                    "links": chunk.links,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        await self._get_client().upsert(collection_name=self._collection, points=points)
