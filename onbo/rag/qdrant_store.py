"""Qdrant implementation of VectorStore with payload-level access filtering."""
from __future__ import annotations

from ..config import Settings
from .store import AccessFilter, Chunk, Hit, VectorStore


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

    async def search(self, query_vector: list[float], access: AccessFilter, limit: int = 5) -> list[Hit]:
        client = self._get_client()
        result = await client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=self._access_filter(access),
            limit=limit,
            with_payload=True,
        )
        hits: list[Hit] = []
        for point in result.points:
            payload = point.payload or {}
            hits.append(
                Hit(
                    text=payload.get("text", ""),
                    source=payload.get("source"),
                    score=point.score,
                    is_qa=payload.get("is_qa", False),
                )
            )
        # Curated Q&A first, then by score.
        hits.sort(key=lambda h: (not h.is_qa, -h.score))
        return hits

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        from qdrant_client import models as qm

        points = [
            qm.PointStruct(
                id=chunk.id,
                vector=vector,
                payload={
                    "text": chunk.text,
                    "source": chunk.source,
                    "is_qa": chunk.is_qa,
                    "department": chunk.department,
                    "roles": chunk.roles,
                    "collection": chunk.collection,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        await self._get_client().upsert(collection_name=self._collection, points=points)
