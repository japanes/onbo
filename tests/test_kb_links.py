"""Deep links on a Q&A pair: normalisation, the text block, and the RAG answer.

Links are a field of the pair (like `roles`), never URLs buried in the answer
text. They leave the pipeline twice: structured on `ActionResult.links`, and as
a plain block appended to the message for channels that only print text.
"""
from __future__ import annotations

from onbo.config import Settings
from onbo.handlers.rag import RagHandler
from onbo.kb.links import normalize_links, render_links
from onbo.rag.store import Hit


class _FakeRetriever:
    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits

    async def search(self, query: str, profile, limit: int = 5) -> list[Hit]:
        return self._hits


def test_normalize_accepts_the_shapes_a_yaml_file_may_use():
    assert normalize_links(None) == []
    assert normalize_links([]) == []
    # A bare URL is its own title — better a long label than a blank one.
    assert normalize_links("https://app/login") == [
        {"title": "https://app/login", "url": "https://app/login"}
    ]
    assert normalize_links([{"title": " Вход ", "url": " https://app/login "}]) == [
        {"title": "Вход", "url": "https://app/login"}
    ]


def test_normalize_drops_items_without_a_url():
    """Half-formed links are dropped rather than stored and rendered blank."""
    assert normalize_links([{"title": "Вход"}, 42, {"url": "https://app/x"}]) == [
        {"title": "https://app/x", "url": "https://app/x"}
    ]


def test_render_block_is_empty_when_there_is_nothing_to_add():
    assert render_links([]) == ""


def test_render_block_lists_one_link_per_line():
    text = render_links(
        [
            {"title": "Вход", "url": "https://app/login"},
            {"title": "Профиль", "url": "https://app/profile"},
        ]
    )
    assert text == (
        "Ссылки:\n- Вход: https://app/login\n- Профиль: https://app/profile"
    )


async def test_answer_returns_links_structured_and_as_a_block(profile):
    handler = RagHandler(Settings())
    handler._retriever = _FakeRetriever(
        [
            Hit(
                text="Ссылка живёт 30 минут.",
                source="Q&A: вход",
                links=[{"title": "Страница входа", "url": "https://app/login"}],
            )
        ]
    )
    result = await handler.answer("не работает ссылка", profile)

    assert result.message.startswith("Ссылка живёт 30 минут.")
    assert result.message.endswith("Ссылки:\n- Страница входа: https://app/login")
    assert [(link.title, link.url) for link in result.links] == [
        ("Страница входа", "https://app/login")
    ]


async def test_answer_without_links_has_no_block(profile):
    handler = RagHandler(Settings())
    handler._retriever = _FakeRetriever([Hit(text="just text", source="s")])
    result = await handler.answer("q", profile)
    assert "Ссылки:" not in result.message
    assert result.links == []
