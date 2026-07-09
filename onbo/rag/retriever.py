"""Retriever: embed the query, search under the profile-derived access filter.

The access filter is built ONLY from the authenticated Profile (department /
roles), never from the query text or an LLM decision — this is the access
control boundary for the knowledge base.
"""
from __future__ import annotations

from ..config import Settings
from ..core.schemas import Profile
from .store import AccessFilter, Hit


class Retriever:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._embedder = None
        self._store = None

    def _get_embedder(self):
        if self._embedder is None:
            from .embeddings import Embedder

            self._embedder = Embedder(self._settings)
        return self._embedder

    def _get_store(self):
        if self._store is None:
            from .qdrant_store import QdrantStore

            self._store = QdrantStore(self._settings)
        return self._store

    @staticmethod
    def _access_from_profile(profile: Profile) -> AccessFilter:
        return AccessFilter(department=profile.department, roles=profile.roles)

    async def search(self, query: str, profile: Profile, limit: int = 5) -> list[Hit]:
        vector = self._get_embedder().encode_one(query)
        access = self._access_from_profile(profile)
        return await self._get_store().search(vector, access, limit=limit)
