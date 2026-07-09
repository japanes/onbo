"""Source connector interface — one plugin per ingestion source.

New sources (Confluence, Notion, ...) are added as new files implementing this
interface; the rest of the KB pipeline is untouched.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class RawDoc(BaseModel):
    source: str  # file path or URL
    title: str | None = None
    body: str


class Source(ABC):
    @abstractmethod
    def fetch(self) -> list[RawDoc]:
        """Return raw documents to be chunked, embedded and indexed."""
        ...
