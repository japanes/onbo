"""KB management: CRUD for collections / documents / Q&A, plus (re)indexing.

Exposed two ways over the same functions: an admin API (mounted under /admin by
the web channel) and the ``onbo kb ...`` CLI. This skeleton implements the
indexing side; persistence into Postgres (kb.models) is wired where marked.
"""
from __future__ import annotations

import os

from ..config import Settings
from .index import Indexer
from .sources.files import FileSource
from .sources.website import WebsiteSource


class KnowledgeBaseAdmin:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._indexer = Indexer(settings)

    async def add_doc(
        self,
        path: str,
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
    ) -> int:
        """Ingest files or a URL into a collection with access tags."""
        source = WebsiteSource(path) if path.startswith(("http://", "https://")) else FileSource(path)
        docs = source.fetch()
        # TODO: persist docs into kb.models.Document before indexing.
        return await self._indexer.index_documents(docs, collection, department, roles)

    async def add_qa(
        self,
        question: str,
        answer: str,
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
    ) -> int:
        # TODO: persist into kb.models.QAPair before indexing.
        return await self._indexer.index_qa(question, answer, collection, department, roles)

    async def seed(self) -> int:
        """Load the starter FAQ from config/seed_faq.yaml so the KB isn't empty."""
        import yaml

        from ..config import config_dir

        path = os.path.join(config_dir(), "seed_faq.yaml")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        count = 0
        for item in data.get("qa", []):
            count += await self.add_qa(
                item["question"],
                item["answer"],
                item.get("collection", "common"),
                item.get("department"),
                item.get("roles"),
            )
        return count

    async def reindex(self) -> int:
        """Rebuild the Qdrant index from the canonical Postgres content."""
        # TODO: read all documents / Q&A from kb.models and re-embed them.
        raise NotImplementedError("reindex requires the Postgres KB store (kb extra).")
