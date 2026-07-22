"""Aggregator merges a multi-action turn into one reply with the right sections."""
from __future__ import annotations

from onbo.core.aggregator import aggregate
from onbo.core.schemas import ActionResult, ResultStatus


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
