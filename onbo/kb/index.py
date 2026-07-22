"""Indexing: chunk -> embed -> upsert into Qdrant with access tags.

Rebuilds the derived Qdrant index from KB content. Q&A pairs are indexed by
their question and marked ``is_qa`` so retrieval can rank them above raw chunks.
"""
from __future__ import annotations

from ..config import Settings
from ..rag.store import Chunk
from .chunker import chunk_text
from .sources.base import RawDoc


class Indexer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._embedder = None
        self._store = None

    def _get_embedder(self):
        if self._embedder is None:
            from ..rag.embeddings import Embedder

            self._embedder = Embedder(self._settings)
        return self._embedder

    def _get_store(self):
        if self._store is None:
            from ..rag.qdrant_store import QdrantStore

            self._store = QdrantStore(self._settings)
        return self._store

    async def reset(self) -> None:
        """Drop the Qdrant collection (used before a full reindex)."""
        await self._get_store().reset()

    async def upsert_chunks(self, chunks: list[Chunk]) -> int:
        """Embed each chunk's text and upsert. Idempotent per ``chunk.id``."""
        return await self._embed_and_upsert(chunks)

    async def upsert_qa_chunk(self, question: str, chunk: Chunk) -> int:
        """Upsert one Q&A point: embedded by the *question*, retrievable text = answer."""
        vector = self._get_embedder().encode_one(question)
        await self._get_store().upsert([chunk], [vector])
        return 1

    def build_doc_chunks(
        self,
        body: str,
        base_id: str,
        source: str,
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
    ) -> list[Chunk]:
        """Chunk a document body into stable-id Chunks (``{base_id}#{i}``)."""
        return [
            Chunk(
                id=f"{base_id}#{i}",
                text=piece,
                source=source,
                department=department,
                roles=roles or [],
                collection=collection,
            )
            for i, piece in enumerate(chunk_text(body))
        ]

    async def index_documents(
        self,
        docs: list[RawDoc],
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
    ) -> int:
        chunks: list[Chunk] = []
        for doc in docs:
            for i, piece in enumerate(chunk_text(doc.body)):
                chunks.append(
                    Chunk(
                        id=f"{doc.source}#{i}",
                        text=piece,
                        source=doc.source,
                        department=department,
                        roles=roles or [],
                        collection=collection,
                    )
                )
        return await self._embed_and_upsert(chunks)

    async def index_qa(
        self,
        question: str,
        answer: str,
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
        video_url: str | None = None,
        links: list[dict] | None = None,
    ) -> int:
        # Embed the question, but store the answer as the retrievable text.
        chunk = Chunk(
            id=f"qa::{collection}::{question}",
            text=answer,
            source=f"Q&A: {question}",
            is_qa=True,
            department=department,
            roles=roles or [],
            collection=collection,
            video_url=video_url,
            links=links or [],
        )
        vector = self._get_embedder().encode_one(question)
        await self._get_store().upsert([chunk], [vector])
        return 1

    async def _embed_and_upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        vectors = self._get_embedder().encode([c.text for c in chunks])
        await self._get_store().upsert(chunks, vectors)
        return len(chunks)
