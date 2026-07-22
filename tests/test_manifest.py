"""llm.json manifest: public-only projection + no internal-wiring leak.

Runs offline: actions/pipelines and the KB are injected, so we assert the
access filter (private Q&A and audience-restricted actions never appear) and
that the ``api:`` blocks / pipeline steps stay internal. The web-endpoint test
uses the real config to prove the route (and its ``.well-known`` alias) serves
valid JSON without leaking an ``api`` block.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from onbo.channels.web import WebChannel
from onbo.config import Settings
from onbo.core.manifest import build_llm_manifest
from onbo.core.schemas import ActionMode, Response
from onbo.handlers.actions.registry import ActionSpec, ApiSpec, ParamSpec, PipelineSpec, PipelineStep


class _FakeKB:
    def __init__(self, qa=None) -> None:
        self._qa = qa or []

    def list_qa(self, collection=None):
        return self._qa


def _actions() -> dict:
    return {
        "change_email": ActionSpec(
            name="change_email",
            description="Сменить email",
            mode=ActionMode.confirm,
            params={"email": ParamSpec(type="email", required=True)},
            api=ApiSpec(method="POST", path="/api/users/{user_id}/email"),  # must NOT leak
        ),
        "reset_password": ActionSpec(
            name="reset_password",
            description="Сбросить пароль",
            sensitive=True,                       # -> mode link
            link_url="https://app.example.com/settings/security",
        ),
        "set_language": ActionSpec(
            name="set_language",
            description="Язык интерфейса",
            params={"lang": ParamSpec(type="enum", values=["ru", "en"])},
        ),
        "ship_order": ActionSpec(
            name="ship_order", description="Отгрузить", department="warehouse"  # private
        ),
    }


def _pipelines() -> dict:
    return {
        "new_order": PipelineSpec(
            name="new_order",
            description="Оформить заказ",
            mode=ActionMode.confirm,
            params={"order_id": ParamSpec(type="string", required=True)},
            steps=[PipelineStep(action="change_email")],  # internal wiring
        ),
    }


def _manifest():
    return build_llm_manifest(
        Settings(product={"name": "Acme", "description": "CRM"}),
        actions=_actions(),
        pipelines=_pipelines(),
        kb_admin=_FakeKB([
            {"collection": "common", "question": "Как войти?", "answer": "…",
             "video_url": None, "department": None, "roles": []},
            {"collection": "acc", "question": "Где счета?", "answer": "…",
             "video_url": None, "department": "accounting", "roles": []},   # private dept
            {"collection": "adm", "question": "Секрет?", "answer": "…",
             "video_url": None, "department": None, "roles": ["admin"]},    # private role
        ]),
    )


def test_manifest_product_and_endpoint():
    m = _manifest()
    assert m["product"] == {"name": "Acme", "description": "CRM"}
    assert m["chat_endpoint"] == "/chat"


def test_manifest_qa_only_public():
    qa = _manifest()["qa"]
    questions = {q["question"] for q in qa}
    assert questions == {"Как войти?"}                # private dept/role dropped
    assert all("department" not in q and "roles" not in q for q in qa)


def test_manifest_actions_public_only_and_no_api_leak():
    m = _manifest()
    names = {a["name"] for a in m["actions"]}
    assert "ship_order" not in names                  # foreign department -> hidden
    assert {"change_email", "reset_password", "set_language"} <= names
    for entry in m["actions"]:
        assert "api" not in entry                     # internal HTTP wiring stays inside


def test_manifest_modes_params_and_link_url():
    actions = {a["name"]: a for a in _manifest()["actions"]}
    assert actions["change_email"]["mode"] == "confirm"
    assert actions["change_email"]["params"]["email"] == {"type": "email", "required": True}
    assert actions["set_language"]["params"]["lang"]["values"] == ["ru", "en"]
    assert actions["reset_password"]["mode"] == "link"
    assert actions["reset_password"]["link_url"].endswith("/settings/security")


def test_manifest_pipelines_present_without_steps():
    pipes = _manifest()["pipelines"]
    assert [p["name"] for p in pipes] == ["new_order"]
    assert pipes[0]["mode"] == "confirm"
    assert "steps" not in pipes[0]                     # step wiring is internal


# -- web route (real config) -------------------------------------------------


class _NullPipeline:
    async def handle(self, env, profile=None):
        return Response(text="", results=[])


def _client() -> TestClient:
    return TestClient(WebChannel(Settings(), _NullPipeline()).build_app())


def test_llm_json_route_and_well_known_alias():
    client = _client()
    for path in ("/llm.json", "/.well-known/llm.json"):
        r = client.get(path)
        assert r.status_code == 200
        body = r.json()
        assert body["chat_endpoint"] == "/chat"
        assert {"product", "qa", "actions", "pipelines"} <= body.keys()
        assert all("api" not in a for a in body["actions"])   # no wiring leak, real config
