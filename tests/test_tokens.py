"""Signed identity tokens: what is accepted, what is refused, and by whom.

The token is the access filter — everything the retrieval layer is allowed to
show follows from the claims inside it. So the interesting tests are the
refusals: a tampered payload, a missing signature, an expired token, and the
`alg: none` trick that turns a signed token into an unsigned one.
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from onbo.auth.tokens import TokenError, decode_token, profile_from_token, sign_token
from onbo.channels.web import WebChannel
from onbo.config import ChannelSettings, Settings
from onbo.core.schemas import Response

SECRET = "test-secret"


def _settings(secret: str = SECRET, allow_user_id: bool = True) -> Settings:
    settings = Settings()
    settings.auth.jwt_secret = secret
    settings.auth.allow_user_id = allow_user_id
    settings.channels = {"web": ChannelSettings(enabled=True, port=18000)}
    return settings


class _EchoPipeline:
    """Records the profile it was handed, so we can see what the token became."""

    def __init__(self) -> None:
        self.profiles = []

    async def handle(self, env, profile=None):
        self.profiles.append(profile)
        return Response(text="ok", results=[])

    async def maybe_welcome(self, user_id, profile=None):
        return None

    async def welcome(self, user_id, profile=None):
        return Response(text="привет", results=[])

    async def confirm(self, user_id, action, approved, profile=None):
        self.profiles.append(profile)
        from onbo.core.schemas import ActionResult, ResultStatus

        return ActionResult(status=ResultStatus.done, action=action, message="готово")


# -- the token itself --------------------------------------------------------


def test_roundtrip_carries_id_department_and_roles():
    token = sign_token("u1", SECRET, department="accounting", roles=["accountant"])
    profile = profile_from_token(token, _settings())
    assert (profile.user_id, profile.department, profile.roles) == (
        "u1", "accounting", ["accountant"]
    )


def test_a_different_secret_is_not_trusted():
    token = sign_token("u1", "other-secret")
    with pytest.raises(TokenError, match="подпись"):
        decode_token(token, SECRET)


def test_editing_the_payload_breaks_the_signature():
    """The whole point: a user cannot promote themselves by rewriting claims."""
    header, payload, signature = sign_token("u1", SECRET, roles=["intern"]).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["roles"] = ["admin"]
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(TokenError, match="подпись"):
        decode_token(f"{header}.{forged}.{signature}", SECRET)


def test_alg_none_is_refused():
    """`alg: none` is the classic bypass: a token with no signature at all."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "u1", "exp": time.time() + 60}).encode()
    ).decode().rstrip("=")
    with pytest.raises(TokenError, match="HS256"):
        decode_token(f"{header}.{payload}.", SECRET)


def test_expired_token_is_refused():
    with pytest.raises(TokenError, match="истёк"):
        decode_token(sign_token("u1", SECRET, ttl=-120), SECRET)


def test_token_without_exp_is_refused():
    """A token that never expires is a permanent key to someone's account."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "u1"}).encode()).decode().rstrip("=")
    import hashlib
    import hmac

    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    with pytest.raises(TokenError, match="exp"):
        decode_token(f"{header}.{payload}.{signature}", SECRET)


def test_token_login_is_off_until_a_secret_is_set():
    with pytest.raises(TokenError, match="jwt_secret"):
        profile_from_token(sign_token("u1", SECRET), _settings(secret=""))


def test_a_single_role_may_be_written_without_a_list():
    """Directories differ: some hand over one role id, not an array of names."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    claims = {"sub": "u1", "dept": "sales", "roles": 7, "exp": int(time.time()) + 60}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    import hashlib
    import hmac

    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    profile = profile_from_token(f"{header}.{payload}.{signature}", _settings())
    assert profile.roles == ["7"] and profile.department == "sales"


# -- the web endpoint --------------------------------------------------------


def test_chat_with_a_token_needs_no_directory_lookup():
    pipeline = _EchoPipeline()
    client = TestClient(WebChannel(_settings(), pipeline).build_app())

    token = sign_token("u1", SECRET, department="sales", roles=["manager"])
    r = client.post("/chat", json={"text": "привет", "token": token})

    assert r.status_code == 200
    profile = pipeline.profiles[0]
    assert (profile.user_id, profile.department, profile.roles) == ("u1", "sales", ["manager"])


def test_a_bad_token_is_401_and_never_reaches_the_pipeline():
    pipeline = _EchoPipeline()
    client = TestClient(WebChannel(_settings(), pipeline).build_app())

    r = client.post("/chat", json={"text": "привет", "token": "не.токен.вовсе"})

    assert r.status_code == 401
    assert pipeline.profiles == []


def test_user_id_is_refused_when_only_tokens_are_allowed():
    """Production shape: the endpoint is public, so a bare id proves nothing."""
    channel = WebChannel(_settings(allow_user_id=False), _EchoPipeline())
    client = TestClient(channel.build_app())
    assert client.post("/chat", json={"text": "привет", "user_id": "u1"}).status_code == 401


def test_neither_id_nor_token_is_a_422():
    client = TestClient(WebChannel(_settings(), _EchoPipeline()).build_app())
    assert client.post("/chat", json={"text": "привет"}).status_code == 422


def test_user_id_still_works_when_no_token_is_given():
    """Proxy mode stays the default: your backend passes the id it already knows."""
    pipeline = _EchoPipeline()
    client = TestClient(WebChannel(_settings(secret=""), pipeline).build_app())

    r = client.post("/chat", json={"text": "привет", "user_id": "u1"})

    assert r.status_code == 200
    assert pipeline.profiles == [None]   # looked up in the users table instead


def test_confirm_carries_the_token_profile_too():
    pipeline = _EchoPipeline()
    client = TestClient(WebChannel(_settings(), pipeline).build_app())
    token = sign_token("u1", SECRET, roles=["manager"])

    r = client.post("/confirm", json={"action": "change_email", "approved": True, "token": token})

    assert r.status_code == 200
    assert pipeline.profiles[0].roles == ["manager"]


# -- CORS --------------------------------------------------------------------


def test_widget_origin_is_allowed_when_listed():
    settings = _settings()
    settings.channels["web"].cors_origins = ["https://app.example.com"]
    client = TestClient(WebChannel(settings, _EchoPipeline()).build_app())

    r = client.post(
        "/chat",
        json={"text": "привет", "token": sign_token("u1", SECRET)},
        headers={"Origin": "https://app.example.com"},
    )
    assert r.headers["access-control-allow-origin"] == "https://app.example.com"


def test_open_cors_without_tokens_refuses_to_start():
    """`*` plus an unauthenticated /chat = any site asking as any employee."""
    settings = _settings(secret="")
    settings.channels["web"].cors_origins = ["*"]
    with pytest.raises(RuntimeError, match="jwt_secret"):
        WebChannel(settings, _EchoPipeline()).build_app()
