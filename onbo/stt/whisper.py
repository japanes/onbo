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

    def _load(self):
        global _model
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - depends on the stt extra
                raise STTUnavailable("faster-whisper не установлен (extra `stt`).") from exc
            _model = WhisperModel(self._settings.stt.model)
        return _model

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        if not self.enabled:
            raise STTUnavailable("Голосовой ввод выключен (stt.enabled: false).")
        model = self._load()
        with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
            tmp.write(audio)
            tmp.flush()
            segments, _ = model.transcribe(tmp.name, language=language)
            return " ".join(segment.text for segment in segments).strip()
