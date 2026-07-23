"""Proactive welcome: a short offline greeting + fire-once first contact.

Everything runs offline: no LLM, no Postgres/Redis (an in-memory welcome-session
fake and a forced db-less path), so we assert the visibility rule, the greeting
shape and the one-shot behaviour with no backend. The command list itself is
`about`'s job now and is tested in test_about.py.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from onbo.channels.web import WebChannel
from onbo.config import Settings, WelcomeSettings
from onbo.core.pipeline import Pipeline
from onbo.core.schemas import Profile, Response, ResultStatus
from onbo.handlers.actions.registry import ActionSpec, spec_visible_to
from onbo.handlers.welcome import WelcomeHandler
from onbo.state import welcome as welcome_state


def _accountant() -> Profile:
    return Profile(user_id="acc1", department="accounting", roles=["accountant"])


# -- audience visibility rule ------------------------------------------------


def test_spec_visible_to_department_and_roles():
    acc = _accountant()
    assert spec_visible_to(ActionSpec(name="pub"), acc)                        # public
    assert spec_visible_to(ActionSpec(name="inv", department="accounting"), acc)
    assert not spec_visible_to(ActionSpec(name="ship", department="warehouse"), acc)
    assert not spec_visible_to(ActionSpec(name="cfg", roles=["admin"]), acc)   # role gate


# -- the greeting itself -----------------------------------------------------


class _SpyLLM:
    """An LLM that must not be called; records it if it is."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return "переписанное приветствие"


async def test_greeting_is_short_and_never_calls_the_llm_by_default():
    llm = _SpyLLM()
    res = await WelcomeHandler(Settings(), llm).answer(_accountant())
    assert res.status == ResultStatus.answer
    assert llm.calls == []                               # smooth is off by default
    assert "ассистент онбординга" in res.message.lower()
    assert "что ты умеешь" in res.message                # points at the command list
    assert len(res.message.splitlines()) <= 4            # short: 3 lines, video aside


async def test_greeting_is_smoothed_only_when_flag_is_on():
    llm = _SpyLLM()
    settings = Settings(welcome=WelcomeSettings(smooth=True))
    res = await WelcomeHandler(settings, llm).answer(_accountant())
    assert len(llm.calls) == 1
    assert res.message == "переписанное приветствие"


async def test_video_line_uses_media_base_url():
    settings = Settings(
        media={"dir": "media", "base_url": "https://app.example.com"},
        welcome=WelcomeSettings(video={"accounting": "/media/welcome/acc.mp4"}),
    )
    res = await WelcomeHandler(settings, llm=None).answer(_accountant())
    assert "Видео-знакомство: https://app.example.com/media/welcome/acc.mp4" in res.message


async def test_text_override_replaces_body_but_keeps_video():
    settings = Settings(
        welcome=WelcomeSettings(
            text_overrides={"accountant": "Добро пожаловать в бухгалтерию!"},
            video={"accountant": "/media/welcome/acc.mp4"},
        ),
    )
    res = await WelcomeHandler(settings, llm=None).answer(_accountant())
    assert res.message.startswith("Добро пожаловать в бухгалтерию!")
    assert "что ты умеешь" not in res.message                # override replaces the text
    assert "Видео-знакомство: /media/welcome/acc.mp4" in res.message


# -- first-contact tracking (db-less fallback) -------------------------------


class _MemWelcomeSession:
    """In-memory stand-in for the welcome-marker side of state.session.Session."""

    def __init__(self) -> None:
        self._welcomed: set[str] = set()

    async def is_welcomed(self, user_id: str) -> bool:
        return user_id in self._welcomed

    async def mark_welcomed(self, user_id: str) -> None:
        self._welcomed.add(user_id)


async def test_first_contact_marked_once(monkeypatch):
    monkeypatch.setattr("onbo.state.db.db_available", lambda: False)  # force session path
    session, settings = _MemWelcomeSession(), Settings()
    assert not await welcome_state.is_welcomed("u1", settings, session)
    await welcome_state.mark_welcomed("u1", settings, session)
    assert await welcome_state.is_welcomed("u1", settings, session)


# -- pipeline wiring ---------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch):
    async def fake_resolve(user_id, settings):
        return Profile(user_id=user_id, department="accounting", roles=["accountant"])

    monkeypatch.setattr("onbo.core.pipeline.resolve_profile", fake_resolve)
    monkeypatch.setattr("onbo.state.db.db_available", lambda: False)
    p = Pipeline()
    p.welcome_handler._llm = None   # template fallback: the digest must stay offline
    p.session = _MemWelcomeSession()
    return p


async def test_maybe_welcome_fires_once(pipeline):
    first = await pipeline.maybe_welcome("acc1")
    assert first is not None and "ассистент онбординга" in first.text.lower()
    assert await pipeline.maybe_welcome("acc1") is None   # already greeted -> silent


async def test_maybe_welcome_none_when_disabled(monkeypatch):
    async def fake_resolve(user_id, settings):
        return Profile(user_id=user_id, roles=["employee"])

    monkeypatch.setattr("onbo.core.pipeline.resolve_profile", fake_resolve)
    monkeypatch.setattr("onbo.state.db.db_available", lambda: False)
    p = Pipeline(Settings(welcome=WelcomeSettings(enabled=False)))
    p.session = _MemWelcomeSession()
    assert await p.maybe_welcome("acc1") is None


# -- web triggers ------------------------------------------------------------


class _WebPipeline:
    """Minimal pipeline for the web endpoints: greets once, echoes chat."""

    def __init__(self) -> None:
        self._welcomed: set[str] = set()

    async def handle(self, env, profile=None):
        return Response(text=f"ответ:{env.text}", results=[])

    async def welcome(self, user_id: str, profile=None) -> Response:
        self._welcomed.add(user_id)
        return Response(text="ЗДРАВСТВУЙ", results=[])

    async def maybe_welcome(self, user_id: str, profile=None):
        if user_id in self._welcomed:
            return None
        return await self.welcome(user_id)


def _web_client(settings, pipeline) -> TestClient:
    return TestClient(WebChannel(settings, pipeline).build_app())


def test_web_welcome_404_when_disabled():
    client = _web_client(Settings(welcome=WelcomeSettings(enabled=False)), _WebPipeline())
    assert client.post("/welcome", json={"user_id": "u1"}).status_code == 404


def test_web_welcome_endpoint_returns_digest():
    r = _web_client(Settings(), _WebPipeline()).post("/welcome", json={"user_id": "u1"})
    assert r.status_code == 200 and "ЗДРАВСТВУЙ" in r.json()["text"]


def test_web_chat_prepends_welcome_once():
    client = _web_client(Settings(), _WebPipeline())
    first = client.post("/chat", json={"user_id": "u1", "text": "привет"}).json()
    assert first["welcomed"] is True
    assert first["text"].startswith("ЗДРАВСТВУЙ") and "ответ:привет" in first["text"]
    second = client.post("/chat", json={"user_id": "u1", "text": "ещё"}).json()
    assert second["welcomed"] is False and "ЗДРАВСТВУЙ" not in second["text"]
