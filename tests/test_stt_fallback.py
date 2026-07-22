"""STT robustness: a broken GPU runtime must fall back to CPU, not crash.

CTranslate2 loads cuBLAS/cuDNN lazily, so a missing GPU runtime surfaces at
encode time (inside transcribe), not at model construction. These tests fake the
WhisperModel to prove both failure points degrade to CPU. No model download.
"""
from __future__ import annotations

import pytest

from onbo.config import Settings
from onbo.stt import whisper as whisper_mod
from onbo.stt.whisper import STT, STTUnavailable


class _Seg:
    def __init__(self, text):
        self.text = text


class FakeModel:
    """Fake WhisperModel: fails at the configured point on non-CPU (and on CPU too
    when ``fail_on_cpu`` is set, to exercise the no-fallback-left path)."""

    fail_at = None  # "construct" | "encode" | None (set per test)
    fail_on_cpu = False

    def __init__(self, model, device, compute_type):
        self.device = device
        if FakeModel.fail_at == "construct" and self._should_fail(device):
            raise RuntimeError("libcublas.so.12 not found")

    @staticmethod
    def _should_fail(device):
        return device != "cpu" or FakeModel.fail_on_cpu

    def transcribe(self, path, language=None):
        if FakeModel.fail_at == "encode" and self._should_fail(self.device):
            raise RuntimeError("libcublas.so.12 not found")
        return [_Seg(f"ok:{self.device}")], None


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    whisper_mod._model = None  # clear the module-level model cache between tests
    FakeModel.fail_at = None
    FakeModel.fail_on_cpu = False
    monkeypatch.setattr(STT, "_whisper", staticmethod(lambda: FakeModel))
    yield
    whisper_mod._model = None


def _stt(device="cuda"):
    s = Settings()
    s.stt.enabled = True
    s.stt.device = device
    return STT(s)


async def test_encode_failure_falls_back_to_cpu():
    FakeModel.fail_at = "encode"
    text = await _stt("cuda").transcribe(b"audio")
    assert text == "ok:cpu"  # retried on CPU after the GPU encode failed


async def test_construction_failure_falls_back_to_cpu():
    FakeModel.fail_at = "construct"
    text = await _stt("cuda").transcribe(b"audio")
    assert text == "ok:cpu"


async def test_cpu_device_has_no_fallback_and_raises():
    # Configured for CPU already: a failure must surface, not silently swallow.
    FakeModel.fail_at = "encode"
    FakeModel.fail_on_cpu = True
    with pytest.raises(RuntimeError):
        await _stt("cpu").transcribe(b"audio")


async def test_disabled_stt_refuses():
    s = Settings()
    s.stt.enabled = False
    with pytest.raises(STTUnavailable):
        await STT(s).transcribe(b"audio")
