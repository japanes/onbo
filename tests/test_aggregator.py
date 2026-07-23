"""Aggregator merges a multi-action turn into one reply with the right sections."""
from __future__ import annotations

from onbo.core.aggregator import aggregate
from onbo.core.schemas import ActionResult, Link, ResultStatus


def test_empty_results_is_friendly():
    resp = aggregate([])
    assert "переформулировать" in resp.text.lower()


def test_multi_action_has_all_sections():
    results = [
        ActionResult(status=ResultStatus.done, message="Язык переключён."),
        ActionResult(status=ResultStatus.needs_confirm, confirm_prompt="Поменять email?"),
        ActionResult(status=ResultStatus.link, message="Смена пароля:", link_url="https://x/sec"),
        ActionResult(status=ResultStatus.dry_run, message="Вызвал бы POST /x."),
        ActionResult(status=ResultStatus.needs_input, message="не хватает: lang"),
        ActionResult(status=ResultStatus.failed, message="«foo» не поддерживается."),
    ]
    text = aggregate(results).text
    assert "Язык переключён." in text
    assert "Демо-режим" in text and "Вызвал бы POST /x." in text
    assert "Нужно подтверждение" in text and "Поменять email?" in text
    assert "https://x/sec" in text
    assert "Не хватает данных" in text
    assert "Не смог выполнить" in text


def test_dry_run_section_present_only_when_needed():
    resp = aggregate([ActionResult(status=ResultStatus.done, message="ok")])
    assert "Демо-режим" not in resp.text
    assert resp.text == "ok"


def test_results_are_carried_through():
    results = [ActionResult(status=ResultStatus.answer, message="hi")]
    resp = aggregate(results)
    assert resp.results == results


def test_links_of_the_whole_turn_come_back_once_at_the_end():
    """One block to strip, wherever the links came from — see kb/links.py.

    A knowledge-base answer glues its own copy to the text (channels that only
    print `message` need it). Aggregated, that copy is a duplicate: the block is
    rebuilt once, last, out of every result's structured links.
    """
    results = [
        ActionResult(
            status=ResultStatus.answer,
            message="Заходите через SSO.\n\nСсылки:\n- Вход: https://app/login",
            links=[Link(title="Вход", url="https://app/login")],
        ),
        ActionResult(
            status=ResultStatus.link,
            message="Сменить пароль: откройте страницу по ссылке.",
            link_url="https://app/profile",
            links=[Link(title="Сменить пароль", url="https://app/profile")],
        ),
    ]
    text = aggregate(results).text
    assert text.count("Ссылки:") == 1
    assert text.endswith(
        "Ссылки:\n- Вход: https://app/login\n- Сменить пароль: https://app/profile"
    )
    # The sentence keeps its words, not a URL glued to the end of it.
    assert "по ссылке.\n" in text or text.count("https://app/profile") == 1


def test_the_same_link_twice_is_listed_once():
    link = Link(title="Профиль", url="https://app/profile")
    results = [
        ActionResult(status=ResultStatus.answer, message="a", links=[link]),
        ActionResult(status=ResultStatus.answer, message="b", links=[link]),
    ]
    assert aggregate(results).text.count("https://app/profile") == 1


def test_a_link_action_without_structured_links_still_shows_its_url():
    """Older/handmade results (link_url only) must not lose the address."""
    results = [
        ActionResult(status=ResultStatus.link, message="Смена пароля:", link_url="https://x/sec")
    ]
    assert "https://x/sec" in aggregate(results).text
