"""Vector store interface and shared data types.

The store is generic over the backend; qdrant_store.py provides the Qdrant
implementation. The access filter (department/roles) is applied server-side so
inaccessible chunks never reach the caller.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


CONTENT = "content"   # knowledge base: what RAG answers from
ACTION = "action"     # a command's own description: what the classifier picks from


class Chunk(BaseModel):
    """A unit of indexed content plus its access tags and provenance.

    ``kind`` says which question this point answers. Knowledge and commands share
    one collection — one embedding model, one reindex — but they must never share
    a search: a command surfacing as the answer to «как оформить отпуск?» is
    nonsense, and a document surfacing as a command is worse. Every search states
    the kind it wants; see :meth:`VectorStore.search`.
    """

    id: str
    text: str
    kind: str = CONTENT
    source: str | None = None
    is_qa: bool = False  # curated Q&A ranks above raw document chunks
    department: str | None = None
    roles: list[str] = Field(default_factory=list)
    collection: str | None = None
    video_url: str | None = None  # optional walkthrough video for a Q&A pair
    # Deep links carried alongside the answer: [{"title": ..., "url": ...}].
    links: list[dict] = Field(default_factory=list)
    # Extra payload keys, stored as-is and never overriding the ones above
    # (the actions index keeps its fingerprint here).
    meta: dict = Field(default_factory=dict)


class Hit(BaseModel):
    """A retrieved chunk with its similarity score.

    For an ``ACTION`` hit ``source`` is the action's name — that is what the
    caller does with it, so it needs no field of its own.
    """

    text: str
    kind: str = CONTENT
    source: str | None = None
    score: float = 0.0
    is_qa: bool = False
    video_url: str | None = None
    links: list[dict] = Field(default_factory=list)


class AccessFilter(BaseModel):
    """Visibility constraint built from the authenticated profile — never from text."""

    department: str | None = None
    roles: list[str] = Field(default_factory=list)


class VectorStore(ABC):
    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        access: AccessFilter,
        limit: int = 5,
        kind: str = CONTENT,
    ) -> list[Hit]:
        ...

    @abstractmethod
    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        ...
