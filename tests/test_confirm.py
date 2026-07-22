"""Confirm flow: park in session, execute only on Ok, clear on Cancel."""
from __future__ import annotations

import pytest

from onbo.core.pipeline import Pipeline
from onbo.core.schemas import Profile, ResultStatus
from onbo.state.session import Session
from tests.conftest import FakeRegistry, FakeSession, RecordingHandler


async def test_session_roundtrip_in_memory(monkeypatch):
    from onbo.config import Settings

    session = Session(Settings())
    # Force the in-memory branch (no Redis server in tests).
    async def _no_client():
        return None
    monkeypatch.setattr(session, "_client", _no_client)

    await session.park("u1", "change_email", {"new_email": "a@b.com"})
    assert await session.pop("u1", "change_email") == {"new_email": "a@b.com"}
    # A pop consumes the parked action — a second pop finds nothing.
    assert await session.pop("u1", "change_email") is None


@pytest.fixture
def pipeline(monkeypatch):
    async def fake_resolve(user_id, settings):
        return Profile(user_id=user_id, department="accounting", roles=["accountant"])
    monkeypatch.setattr("onbo.core.pipeline.resolve_profile", fake_resolve)
    p = Pipeline()
    return p


async def test_confirm_ok_executes(pipeline):
    handler = RecordingHandler()
    pipeline.registry = FakeRegistry(handler)
    pipeline.session = FakeSession()
    await pipeline.session.park("u1", "change_email", {"new_email": "a@b.com"})

    res = await pipeline.confirm("u1", "change_email", approved=True)
    assert res.status == ResultStatus.done
    assert handler.calls and handler.calls[0][1] == {"new_email": "a@b.com"}


async def test_confirm_cancel_clears_and_does_not_execute(pipeline):
    handler = RecordingHandler()
    pipeline.registry = FakeRegistry(handler)
    pipeline.session = FakeSession()
    await pipeline.session.park("u1", "change_email", {"new_email": "a@b.com"})

    res = await pipeline.confirm("u1", "change_email", approved=False)
    assert res.status == ResultStatus.done
    assert "Отменено" in res.message
    assert handler.calls == []
    # Cancel consumed the parked action.
    assert await pipeline.session.pop("u1", "change_email") is None


async def test_confirm_without_parked_action_fails(pipeline):
    pipeline.session = FakeSession()
    res = await pipeline.confirm("u1", "change_email", approved=True)
    assert res.status == ResultStatus.failed
    assert "Нет действия" in res.message
