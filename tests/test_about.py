"""«Что ты умеешь»: the command list, grouped, access-filtered and LLM-free.

This is where the catalogue moved when the greeting became three lines, so the
tests that used to guard the welcome digest live here: a person sees exactly the
actions and knowledge-base sections they may use, and asking costs one query.
"""
from __future__ import annotations

from onbo.config import Settings
from onbo.core.schemas import ActionMode, Profile, ResultStatus
from onbo.handlers.about import AboutHandler
from onbo.handlers.actions.registry import ActionSpec


def _accountant() -> Profile:
    return Profile(user_id="acc1", department="accounting", roles=["accountant"])


def _specs() -> dict:
    return {
        "make_invoice": ActionSpec(
            name="make_invoice", description="Выставить счёт", department="accounting"
        ),
        "ship_order": ActionSpec(
            name="ship_order", description="Отгрузить заказ", department="warehouse"
        ),
        "read_docs": ActionSpec(name="read_docs", description="Открыть справку"),  # public
        "drop_project": ActionSpec(
            name="drop_project", description="Удалить проект", mode=ActionMode.confirm
        ),
    }


class _FakeKB:
    def __init__(self, collections=None) -> None:
        self._collections = collections or []

    def list_collections(self):
        return self._collections


async def test_lists_only_visible_actions():
    res = await AboutHandler(Settings(), _specs()).answer(_accountant())
    assert res.status == ResultStatus.answer
    assert "Выставить счёт" in res.message         # own department -> shown
    assert "Открыть справку" in res.message          # public -> shown
    assert "Отгрузить заказ" not in res.message      # foreign department -> hidden
    assert "(3)" in res.message                      # and counted


async def test_actions_are_grouped_by_mode():
    res = await AboutHandler(Settings(), _specs()).answer(_accountant())
    body = res.message
    assert "Сделаю сразу:" in body and "Сделаю после вашего подтверждения:" in body
    # «Удалить проект» must sit under the confirmation header, not the immediate one.
    assert body.index("Сделаю после вашего подтверждения:") < body.index("Удалить проект")


async def test_kb_sections_are_filtered_by_access():
    kb = _FakeKB([
        {"id": 1, "name": "common", "department": None, "roles": []},
        {"id": 2, "name": "acc", "department": "accounting", "roles": []},
        {"id": 3, "name": "wh", "department": "warehouse", "roles": []},
        {"id": 4, "name": "hr-only", "department": None, "roles": ["hr"]},
    ])
    res = await AboutHandler(Settings(), {}, kb).answer(_accountant())
    assert "common" in res.message and "acc" in res.message
    assert "wh" not in res.message.split("Разделы базы знаний")[1]
    assert "hr-only" not in res.message              # role gate, same rule as the KB


async def test_survives_a_kb_that_is_not_there():
    class _Broken:
        def list_collections(self):
            raise RuntimeError("no database")

    res = await AboutHandler(Settings(), _specs(), _Broken()).answer(_accountant())
    assert "Выставить счёт" in res.message           # the rest of `about` still answers
    assert "Разделы базы знаний" not in res.message


async def test_no_actions_says_so_instead_of_an_empty_list():
    res = await AboutHandler(Settings(), {}).answer(_accountant())
    assert "Действий, доступных вам, сейчас нет." in res.message
