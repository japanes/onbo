"""Config-driven HTTP action: honest dry-run + real request + templating."""
from __future__ import annotations

import httpx
import pytest

from onbo.config import ProductSettings, Settings
from onbo.core.schemas import Profile, ResultStatus
from onbo.handlers.actions import http_action
from onbo.handlers.actions.registry import ActionSpec, ApiSpec


def _spec(api: ApiSpec | None):
    return ActionSpec(name="set_language", description="Сменить язык", api=api)


PROFILE = Profile(user_id="acc1", department="accounting", roles=["accountant"])


def _patch_settings(monkeypatch, base_url=""):
    settings = Settings()
    settings.product = ProductSettings(base_url=base_url, api_key="")
    monkeypatch.setattr(http_action, "load_settings", lambda: settings)


async def test_dry_run_when_no_api(monkeypatch):
    _patch_settings(monkeypatch, base_url="")
    res = await http_action.call_product_api(_spec(None), PROFILE, {})
    assert res.status == ResultStatus.dry_run
    assert "не задан вызов API" in res.message


async def test_dry_run_when_no_backend(monkeypatch):
    _patch_settings(monkeypatch, base_url="")
    api = ApiSpec(method="POST", path="/api/users/{user_id}/language")
    res = await http_action.call_product_api(_spec(api), PROFILE, {"lang": "en"})
    assert res.status == ResultStatus.dry_run
    assert "Вызвал бы POST /api/users/acc1/language" in res.message


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    """Records the outgoing request and returns a canned status code."""

    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None, params=None, headers=None):
        _FakeClient.calls.append(
            {"method": method, "url": url, "json": json, "params": params, "headers": headers}
        )
        return _FakeResp(_FakeClient.status)


async def test_real_call_success_templates_body_and_path(monkeypatch):
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    api = ApiSpec(
        method="POST",
        path="/api/users/{user_id}/language",
        body={"language": "{lang}"},
        success_message="Язык: {lang}.",
    )
    res = await http_action.call_product_api(_spec(api), PROFILE, {"lang": "en"})
    assert res.status == ResultStatus.done
    assert res.message == "Язык: en."
    call = _FakeClient.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://backend:9000/api/users/acc1/language"
    assert call["json"] == {"language": "en"}


async def test_absolute_url_needs_no_product_base(monkeypatch):
    # The whole point of `url:` — onbo is installed against an unknown API, so the
    # action file alone must be able to say where the request goes.
    _patch_settings(monkeypatch, base_url="")
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    api = ApiSpec(
        method="POST",
        url="https://app.example.com/api/users/{user_id}/projects",
        body={"name": "{name}"},
    )
    res = await http_action.call_product_api(_spec(api), PROFILE, {"name": "Арбуз"})
    assert res.status == ResultStatus.done
    call = _FakeClient.calls[0]
    assert call["url"] == "https://app.example.com/api/users/acc1/projects"
    assert call["json"] == {"name": "Арбуз"}


async def test_absolute_url_wins_over_path(monkeypatch):
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    api = ApiSpec(url="https://elsewhere.example.com/v2/projects", path="/api/projects")
    res = await http_action.call_product_api(_spec(api), PROFILE, {})
    assert res.status == ResultStatus.done
    assert _FakeClient.calls[0]["url"] == "https://elsewhere.example.com/v2/projects"


async def test_real_call_backend_error_is_failed(monkeypatch):
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    _FakeClient.calls = []
    _FakeClient.status = 500
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    api = ApiSpec(method="POST", path="/api/users/{user_id}/language", body={"language": "{lang}"})
    res = await http_action.call_product_api(_spec(api), PROFILE, {"lang": "en"})
    assert res.status == ResultStatus.failed
    assert "500" in res.message


async def test_the_callers_own_credential_wins_over_the_service_key(monkeypatch):
    # Carried in the signed token: the request then runs as that person, so the
    # product's own permission checks still apply to whatever the assistant does.
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    settings = http_action.load_settings()
    settings.product.api_key = "service-key"
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    profile = Profile(user_id="acc1", product_token="the-users-own-key")
    api = ApiSpec(method="POST", path="/api/projects")
    res = await http_action.call_product_api(_spec(api), profile, {})
    assert res.status == ResultStatus.done
    assert _FakeClient.calls[0]["headers"]["Authorization"] == "Bearer the-users-own-key"


async def test_service_key_is_the_fallback_without_one(monkeypatch):
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    settings = http_action.load_settings()
    settings.product.api_key = "service-key"
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    res = await http_action.call_product_api(_spec(ApiSpec(path="/api/projects")), PROFILE, {})
    assert res.status == ResultStatus.done
    assert _FakeClient.calls[0]["headers"]["Authorization"] == "Bearer service-key"


async def test_context_headers_ride_along_with_the_request(monkeypatch):
    # Without them the product answers in whatever context it defaults to — the
    # action then lands in the wrong workspace and looks like it did nothing.
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    profile = Profile(user_id="acc1", product_headers={"Cookie": "active_account=1"})
    res = await http_action.call_product_api(_spec(ApiSpec(path="/api/projects")), profile, {})
    assert res.status == ResultStatus.done
    assert _FakeClient.calls[0]["headers"]["Cookie"] == "active_account=1"


async def test_context_headers_cannot_replace_the_credential(monkeypatch):
    """Otherwise the context claim becomes a way to act as someone else."""
    _patch_settings(monkeypatch, base_url="http://backend:9000")
    _FakeClient.calls = []
    _FakeClient.status = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    profile = Profile(
        user_id="acc1",
        product_token="the-users-own-key",
        product_headers={"Authorization": "Bearer somebody-elses-key"},
    )
    await http_action.call_product_api(_spec(ApiSpec(path="/api/projects")), profile, {})
    assert _FakeClient.calls[0]["headers"]["Authorization"] == "Bearer the-users-own-key"


def test_a_credential_never_leaks_into_logs_or_records():
    """It ends up in reprs and dumps otherwise — both routinely get written down."""
    profile = Profile(user_id="acc1", product_token="the-users-own-key")
    assert "the-users-own-key" not in repr(profile)
    assert "product_token" not in profile.model_dump()
    assert "the-users-own-key" not in profile.model_dump_json()


def test_render_leaves_unknown_placeholders_untouched():
    # A missing key must not crash — the raw template survives.
    assert http_action._render("{missing}", {"user_id": "u1"}) == "{missing}"
    assert http_action._render("hi {user_id}", {"user_id": "u1"}) == "hi u1"
    assert http_action._render(42, {}) == 42
