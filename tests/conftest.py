"""Shared fakes and fixtures for the offline test suite.

Every test here runs without Postgres / Qdrant / Redis / an LLM: heavy backends
are replaced by in-memory fakes, and the one real backend we exercise (Qdrant's
access filter) uses qdrant-client's ``:memory:`` local mode.
"""
from __future__ import annotations

import pytest

from onbo.core.schemas import ActionResult, Profile, ResultStatus


class FakeSession:
    """In-memory stand-in for state.session.Session (no Redis)."""

    def __init__(self) -> None:
        self.parked: dict[tuple[str, str], dict] = {}
        self.awaiting: dict[str, dict] = {}

    async def park(self, user_id: str, action: str, entities: dict) -> None:
        self.parked[(user_id, action)] = entities

    async def pop(self, user_id: str, action: str) -> dict | None:
        return self.parked.pop((user_id, action), None)

    async def park_input(
        self, user_id: str, action: str, entities: dict, wanted: list[str] | None = None
    ) -> None:
        self.awaiting[user_id] = {"action": action, "entities": entities, "wanted": wanted or []}

    async def pop_input(self, user_id: str) -> dict | None:
        return self.awaiting.pop(user_id, None)


class RecordingHandler:
    """Action handler that records what it executed; returns a `done` result."""

    def __init__(self) -> None:
        self.spec = None
        self.calls: list[tuple[Profile, dict]] = []

    async def validate(self, entities: dict) -> dict:
        return entities

    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        self.calls.append((profile, entities))
        return ActionResult(status=ResultStatus.done, action="rec", message="выполнено")


class FakeRegistry:
    def __init__(self, handler=None) -> None:
        self._handler = handler

    def get(self, name: str):
        return self._handler


@pytest.fixture
def profile() -> Profile:
    return Profile(user_id="u1", department="accounting", roles=["accountant"])
