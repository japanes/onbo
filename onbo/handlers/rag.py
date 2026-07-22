"""RAG handler: retrieve knowledge under the access filter, answer with citations."""
from __future__ import annotations

from ..config import Settings
from ..core.schemas import ActionResult, Profile, ResultStatus


class RagHandler:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._retriever = None  # lazily built (heavy: embeddings + Qdrant)

    def _get_retriever(self):
        if self._retriever is None:
            from ..rag.retriever import Retriever

            self._retriever = Retriever(self._settings)
        return self._retriever

    async def answer(self, query: str, profile: Profile) -> ActionResult:
        try:
            hits = await self._get_retriever().search(query, profile)
        except Exception as exc:  # retriever backend unavailable (no extras / no server)
            return ActionResult(
                status=ResultStatus.failed,
                message=f"База знаний сейчас недоступна: {exc}",
            )
        if not hits:
            return ActionResult(
                status=ResultStatus.answer,
                message="По вашему запросу ничего не нашлось в доступной базе знаний.",
            )
        # Skeleton: surface the top hit and cite sources. A fuller implementation
        # would ask the LLM to compose an answer grounded in the retrieved hits.
        top = hits[0]
        message = top.text
        if getattr(top, "video_url", None):
            message += f"\n\nВидео-инструкция: {self._media_url(top.video_url)}"
        citations = [h.source for h in hits if h.source]
        return ActionResult(status=ResultStatus.answer, message=message, citations=citations)

    def _media_url(self, url: str) -> str:
        """Prefix a site-relative /media path with media.base_url (for non-web channels)."""
        base = self._settings.media.base_url
        if base and url.startswith("/"):
            return base.rstrip("/") + url
        return url
