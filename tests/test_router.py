"""Router: each action mode maps to the right result, sensitive never executes."""
from __future__ import annotations

import pytest

from onbo.core.router import Router
from onbo.core.schemas import (
    ActionMode,
    ActionType,
    ClassifiedAction,
    ResultStatus,
)
from onbo.handlers.actions.registry import ActionSpec, ParamSpec
from tests.conftest import FakeRegistry, FakeSession, RecordingHandler


def _actions():
    return {
        "set_language": ActionSpec(
            name="set_language",
            description="Сменить язык",
            mode=ActionMode.chat,
            params={"lang": ParamSpec(type="enum", required=True, values=["ru", "en"])},
        ),
        "change_email": ActionSpec(
            name="change_email",
            description="Сменить email",
            mode=ActionMode.confirm,
            confirm_prompt="Поменять email на {new_email}?",
            params={"new_email": ParamSpec(type="email", required=True)},
        ),
        "change_password": ActionSpec(
            name="change_password",
            description="Сменить пароль",
            sensitive=True,
            link_url="https://app.example.com/security",
        ),
    }


class FakeRag:
    async def answer(self, query, profile):
        from onbo.core.schemas import ActionResult
        return ActionResult(status=ResultStatus.answer, message=f"rag:{query}")


class FakeAbout:
    async def answer(self, profile):
        from onbo.core.schemas import ActionResult
        return ActionResult(status=ResultStatus.answer, message="about")


def _router(handler=None, session=None):
    return Router(_actions(), FakeRegistry(handler), FakeRag(), FakeAbout(), session or FakeSession())


async def test_sensitive_returns_link_and_never_executes(profile):
    handler = RecordingHandler()
    r = _router(handler)
    res = await r.route(
        ClassifiedAction(type=ActionType.profile_action, action="change_password"), profile
    )
    assert res.status == ResultStatus.link
    assert res.link_url == "https://app.example.com/security"
    assert handler.calls == []  # sensitive path must not run the handler


async def test_confirm_parks_and_asks(profile):
    session = FakeSession()
    r = _router(RecordingHandler(), session)
    res = await r.route(
        ClassifiedAction(
            type=ActionType.profile_action,
            action="change_email",
            entities={"new_email": "a@b.com"},
        ),
        profile,
    )
    assert res.status == ResultStatus.needs_confirm
    assert res.confirm_prompt == "Поменять email на a@b.com?"
    assert session.parked[(profile.user_id, "change_email")] == {"new_email": "a@b.com"}


async def test_missing_required_param_needs_input(profile):
    r = _router(RecordingHandler())
    res = await r.route(
        ClassifiedAction(type=ActionType.profile_action, action="set_language"), profile
    )
    assert res.status == ResultStatus.needs_input
    assert "lang" in res.message


async def test_chat_mode_executes(profile):
    handler = RecordingHandler()
    r = _router(handler)
    res = await r.route(
        ClassifiedAction(
            type=ActionType.profile_action, action="set_language", entities={"lang": "en"}
        ),
        profile,
    )
    assert res.status == ResultStatus.done
    assert handler.calls and handler.calls[0][1] == {"lang": "en"}


async def test_unsupported_action_fails(profile):
    r = _router(RecordingHandler())
    res = await r.route(
        ClassifiedAction(type=ActionType.profile_action, action="does_not_exist"), profile
    )
    assert res.status == ResultStatus.failed
    assert "не поддерживается" in res.message


async def test_unknown_type_fails(profile):
    r = _router()
    res = await r.route(ClassifiedAction(type=ActionType.unknown), profile)
    assert res.status == ResultStatus.failed


async def test_rag_and_about_delegate(profile):
    r = _router()
    rag = await r.route(
        ClassifiedAction(type=ActionType.rag_query, query="возврат"), profile
    )
    assert rag.status == ResultStatus.answer and rag.message == "rag:возврат"
    about = await r.route(ClassifiedAction(type=ActionType.about), profile)
    assert about.message == "about"
