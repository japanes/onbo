"""RagHandler surfaces a Q&A pair's walkthrough video in the answer text."""
from __future__ import annotations

from onbo.config import Settings
from onbo.handlers.rag import RagHandler
from onbo.rag.store import Hit


class _FakeRetriever:
    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits

    async def search(self, query: str, profile, limit: int = 5) -> list[Hit]:
        return self._hits


async def test_answer_appends_video_line(profile):
    handler = RagHandler(Settings())
    handler._retriever = _FakeRetriever(
        [Hit(text="Откройте админку.", source="Q&A: как", video_url="/media/kb/1.mp4")]
    )
    result = await handler.answer("как добавить видео", profile)
    assert "Откройте админку." in result.message
    assert "Видео-инструкция: /media/kb/1.mp4" in result.message


async def test_answer_prefixes_base_url_for_non_web_channels(profile):
    handler = RagHandler(Settings(media={"base_url": "https://app.example.com"}))
    handler._retriever = _FakeRetriever(
        [Hit(text="text", source="s", video_url="/media/kb/1.mp4")]
    )
    result = await handler.answer("q", profile)
    assert "Видео-инструкция: https://app.example.com/media/kb/1.mp4" in result.message


async def test_answer_without_video_has_no_video_line(profile):
    handler = RagHandler(Settings())
    handler._retriever = _FakeRetriever([Hit(text="just text", source="s")])
    result = await handler.answer("q", profile)
    assert "Видео-инструкция" not in result.message
