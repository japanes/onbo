"""Vector store interface and shared data types.

The store is generic over the backend; qdrant_store.py provides the Qdrant
implementation. The access filter (department/roles) is applied server-side so
inaccessible chunks never reach the caller.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A unit of indexed content plus its access tags and provenance."""

    id: str
    text: str
    source: str | None = None
    is_qa: bool = False  # curated Q&A ranks above raw document chunks
    department: str | None = None
    roles: list[str] = Field(default_factory=list)
    collection: str | None = None
    video_url: str | None = None  # optional walkthrough video for a Q&A pair


class Hit(BaseModel):
    """A retrieved chunk with its similarity score."""

    text: str
    source: str | None = None
    score: float = 0.0
    is_qa: bool = False
    video_url: str | None = None


class AccessFilter(BaseModel):
    """Visibility constraint built from the authenticated profile — never from text."""

    department: str | None = None
    roles: list[str] = Field(default_factory=list)


class VectorStore(ABC):
    @abstractmethod
    async def search(self, query_vector: list[float], access: AccessFilter, limit: int = 5) -> list[Hit]:
        ...

    @abstractmethod
    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        ...
