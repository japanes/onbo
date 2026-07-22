"""Voice plumbing: the accept-voice gate + audio -> STT -> pipeline routing.

STT itself is stubbed (no model download in CI); this proves the wiring: a voice
message is transcribed and the transcript flows through the normal text path, and
that voice is politely refused when disabled.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from onbo.channels.web import WebChannel
from onbo.config import ChannelSettings, Settings
from onbo.core.schemas import Response


class FakePipeline:
    async def handle(self, env):
        # Echo the routed text so the test can assert the transcript reached here.
        return Response(text=f"ROUTED:{env.text}")

    async def confirm(self, *a, **k):  # unused here
        ...


def _channel(voice_on: bool) -> WebChannel:
    settings = Settings()
    settings.stt.enabled = voice_on
    settings.channels = {"web": ChannelSettings(enabled=True, accept_voice=voice_on, port=18000)}
    return WebChannel(settings, FakePipeline())


def test_accepts_voice_reflects_both_flags():
    assert _channel(True).accepts_voice() is True
    off = _channel(True)
    off.settings.stt.enabled = False
    assert off.accepts_voice() is False


def test_voice_endpoint_transcribes_and_routes(monkeypatch):
    ch = _channel(True)

    async def fake_transcribe(audio: bytes, locale=None):
        assert audio == b"OGGDATA"  # the uploaded bytes reached STT
        return "смени язык на английский"

    monkeypatch.setattr(ch, "transcribe", fake_transcribe)
    client = TestClient(ch.build_app())

    r = client.post(
        "/voice",
        data={"user_id": "u1", "locale": "ru"},
        files={"audio": ("v.ogg", b"OGGDATA", "audio/ogg")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "смени язык на английский"
    assert body["text"] == "ROUTED:смени язык на английский"


def test_voice_disabled_is_polite(monkeypatch):
    ch = _channel(False)
    client = TestClient(ch.build_app())
    r = client.post(
        "/voice",
        data={"user_id": "u1", "locale": "ru"},
        files={"audio": ("v.ogg", b"x", "audio/ogg")},
    )
    assert r.status_code == 200
    assert "текстом" in r.json()["text"]  # asks the user to type instead
