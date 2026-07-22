"""Feature flags: subsystem mounts appear/disappear, classifier respects them.

Offline: the web tests hit a real FastAPI app (no backend needed), the
classifier tests force the no-LLM fallback so routing is deterministic.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from onbo.channels.web import WebChannel
from onbo.config import FeatureSettings, Settings
from onbo.core.classifier import Classifier
from onbo.core.schemas import ActionType, Envelope, Profile, Response
from onbo.handlers.actions.registry import ActionSpec, ParamSpec


class _NullPipeline:
    async def handle(self, env, profile=None):
        return Response(text="", results=[])

    async def maybe_welcome(self, user_id, profile=None):
        return None

    async def welcome(self, user_id, profile=None):
        return Response(text="hi", results=[])


def _client(features: FeatureSettings) -> TestClient:
    return TestClient(WebChannel(Settings(features=features), _NullPipeline()).build_app())


# -- web mounts --------------------------------------------------------------


def test_admin_mount_removed_when_disabled():
    assert _client(FeatureSettings(admin=False)).get("/admin").status_code == 404
    assert _client(FeatureSettings(admin=True)).get("/admin").status_code == 200


def test_minimal_only_llm_manifest():
    features = FeatureSettings(
        chat=False, admin=False, media=False, welcome=False, actions=False,
        rag=False, llm_manifest=True,
    )
    client = _client(features)
    assert client.get("/llm.json").status_code == 200          # the one thing kept
    assert client.post("/chat", json={"user_id": "u", "text": "hi"}).status_code == 404
    assert client.post("/welcome", json={"user_id": "u"}).status_code == 404
    assert client.get("/admin").status_code == 404


def test_llm_manifest_mount_removed_when_disabled():
    assert _client(FeatureSettings(llm_manifest=False)).get("/llm.json").status_code == 404


# -- classifier gating (no-LLM fallback) -------------------------------------


class _NoLLM:
    async def structured(self, messages, schema):
        raise RuntimeError("no llm configured")   # force the heuristic fallback


def _env(text: str) -> Envelope:
    return Envelope(user_id="u", channel="web", text=text)


async def test_actions_disabled_falls_through_to_rag():
    actions = {"change_email": ActionSpec(
        name="change_email", description="Сменить email",
        params={"email": ParamSpec(type="email")},
    )}
    clf = Classifier(_NoLLM(), actions, actions_enabled=False, rag_enabled=True)
    result = await clf.classify(_env("сменить email на a@b.com"), Profile(user_id="u"))
    # No profile action offered; the message becomes a KB question instead.
    assert [a.type for a in result.actions] == [ActionType.rag_query]


async def test_actions_enabled_still_matches():
    actions = {"change_email": ActionSpec(
        name="change_email", description="Сменить email",
        params={"email": ParamSpec(type="email")},
    )}
    clf = Classifier(_NoLLM(), actions, actions_enabled=True, rag_enabled=True)
    result = await clf.classify(_env("сменить email на a@b.com"), Profile(user_id="u"))
    assert [a.type for a in result.actions] == [ActionType.profile_action]


async def test_rag_disabled_drops_questions():
    clf = Classifier(_NoLLM(), {}, actions_enabled=True, rag_enabled=False)
    result = await clf.classify(_env("как оформить отпуск?"), Profile(user_id="u"))
    assert result.actions == []   # actions-only assistant: free-text questions dropped
