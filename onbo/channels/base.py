"""Channel adapter interface — one plugin per channel.

An adapter converts an inbound message into the unified Envelope (running STT
first when the message is audio and voice is enabled), hands it to the pipeline,
and renders the aggregated Response back — including confirm cards (Ok/Cancel)
for ``mode: confirm`` actions.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Settings
from ..core.pipeline import Pipeline
from ..core.schemas import Envelope, Profile, Response


class Channel(ABC):
    name: str = "base"

    def __init__(self, settings: Settings, pipeline: Pipeline) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self._stt = None

    def _channel_config(self):
        return self.settings.channels.get(self.name)

    def accepts_voice(self) -> bool:
        cfg = self._channel_config()
        return bool(self.settings.stt.enabled and cfg and cfg.accept_voice)

    async def transcribe(self, audio: bytes, locale: str | None = None) -> str:
        """Run shared STT for this channel's audio (gated by accepts_voice)."""
        if self._stt is None:
            from ..stt.whisper import STT

            self._stt = STT(self.settings)
        return await self._stt.transcribe(audio, language=locale)

    @abstractmethod
    async def start(self) -> None:
        """Begin serving (long-poll / webhook / web server)."""
        ...

    def build_envelope(
        self, user_id: str, text: str, locale: str = "ru", ts: str | None = None
    ) -> Envelope:
        return Envelope(user_id=user_id, channel=self.name, text=text, locale=locale, ts=ts)

    async def handle_text(
        self,
        user_id: str,
        text: str,
        locale: str = "ru",
        profile: Profile | None = None,
        ts: str | None = None,
    ) -> Response:
        # ``ts`` is the caller's local clock, passed through untouched: a channel
        # that has one (the web widget) says what date "завтра" means for this
        # person; one that hasn't (Telegram) leaves it None and the server's own
        # time is used. See core/clock.py.
        env = self.build_envelope(user_id, text, locale, ts)
        return await self.pipeline.handle(env, profile)
