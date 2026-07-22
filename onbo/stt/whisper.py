"""Speech-to-text (faster-whisper) — a SHARED service, not a channel.

Any channel calls this when it receives audio, gated by two flags: the global
``stt.enabled`` and the channel's ``accept_voice``. The heavy model is imported
lazily and cached, so importing this module costs nothing until first use.
"""
from __future__ import annotations

import tempfile

from ..config import Settings

_model = None  # cached WhisperModel instance


class STTUnavailable(RuntimeError):
    """Raised when STT is disabled or faster-whisper is not installed."""


class STT:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.stt.enabled

    @staticmethod
    def _whisper():
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on the stt extra
            raise STTUnavailable("faster-whisper не установлен (extra `stt`).") from exc
        return WhisperModel

    def _load(self):
        global _model
        if _model is None:
            WhisperModel = self._whisper()
            stt = self._settings.stt
            try:
                # Prefer the configured device (e.g. the local GPU: device="cuda").
                _model = WhisperModel(stt.model, device=stt.device, compute_type=stt.compute_type)
            except Exception:  # noqa: BLE001 - CUDA/cuDNN missing at construction time
                _model = self._cpu_fallback()
        return _model

    def _cpu_fallback(self):
        """Load the model on CPU so a missing GPU runtime never breaks voice."""
        global _model
        if self._settings.stt.device == "cpu":
            raise  # already on CPU — nothing left to fall back to
        _model = self._whisper()(self._settings.stt.model, device="cpu", compute_type="int8")
        return _model

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        if not self.enabled:
            raise STTUnavailable("Голосовой ввод выключен (stt.enabled: false).")
        model = self._load()
        with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
            tmp.write(audio)
            tmp.flush()
            try:
                segments, _ = model.transcribe(tmp.name, language=language)
            except Exception:  # noqa: BLE001
                # CTranslate2 loads cuBLAS/cuDNN lazily at encode time, so a broken
                # GPU runtime surfaces HERE, not at construction. Retry once on CPU.
                model = self._cpu_fallback()
                segments, _ = model.transcribe(tmp.name, language=language)
            return " ".join(segment.text for segment in segments).strip()
