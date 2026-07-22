"""Telegram adapter — hermetic wiring tests with a fake aiogram.

aiogram is stubbed via sys.modules so no bot token, network, or long-poll is
needed. This proves the closures the channel registers: the missing-token guard,
text routing, confirm-card rendering, the voice gate, and Ok/Cancel parsing.
"""
from __future__ import annotations

import io
import sys
import types as pytypes

import pytest

from onbo.channels.telegram import TelegramChannel
from onbo.config import ChannelSettings, Settings
from onbo.core.schemas import ActionResult, Response, ResultStatus


# --- fake aiogram --------------------------------------------------------------
class FakeBot:
    def __init__(self, token):
        self.token = token

    async def get_file(self, file_id):
        return pytypes.SimpleNamespace(file_path=f"path/{file_id}")

    async def download_file(self, path):
        return io.BytesIO(b"OGGDATA")


class FakeDispatcher:
    last = None

    def __init__(self):
        self.msg = {}  # filter ("voice"/"text") -> handler
        self.cb = None
        FakeDispatcher.last = self

    def message(self, flt):
        def deco(fn):
            self.msg[flt] = fn
            return fn

        return deco

    def callback_query(self, flt):
        def deco(fn):
            self.cb = fn
            return fn

        return deco

    async def start_polling(self, bot):  # no network — return immediately
        return None


class _Data:
    def startswith(self, prefixes):
        return ("cb", prefixes)


class FakeF:
    voice = "voice"
    text = "text"
    data = _Data()


class FakeKb:
    def __init__(self, **kw):
        self.kw = kw


class FakeCommandStart:
    """Stand-in for aiogram.filters.CommandStart (used only as a handler key)."""

    def __init__(self, *args, **kwargs):
        pass


def _install_aiogram(monkeypatch):
    aiogram = pytypes.ModuleType("aiogram")
    aiogram.Bot = FakeBot
    aiogram.Dispatcher = FakeDispatcher
    aiogram.F = FakeF
    types_mod = pytypes.ModuleType("aiogram.types")
    types_mod.CallbackQuery = object
    types_mod.Message = object
    types_mod.InlineKeyboardButton = FakeKb
    types_mod.InlineKeyboardMarkup = FakeKb
    filters_mod = pytypes.ModuleType("aiogram.filters")
    filters_mod.CommandStart = FakeCommandStart
    monkeypatch.setitem(sys.modules, "aiogram", aiogram)
    monkeypatch.setitem(sys.modules, "aiogram.types", types_mod)
    monkeypatch.setitem(sys.modules, "aiogram.filters", filters_mod)


# --- fake telegram message/query ----------------------------------------------
class FakeMessage:
    def __init__(self, text=None, voice=None, uid="42", lang="ru"):
        self.text = text
        self.voice = voice
        self.from_user = pytypes.SimpleNamespace(id=uid, language_code=lang)
        self.sent = []  # (text, reply_markup)

    async def answer(self, text, reply_markup=None):
        self.sent.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data, uid="42"):
        self.data = data
        self.from_user = pytypes.SimpleNamespace(id=uid)
        self.message = FakeMessage()
        self.answered = False

    async def answer(self):
        self.answered = True


# --- fake pipeline -------------------------------------------------------------
class FakePipeline:
    def __init__(self, response=None):
        self._response = response or Response(text="hi", results=[])
        self.confirm_calls = []

    async def handle(self, env, profile=None):
        return self._response

    async def maybe_welcome(self, user_id, profile=None):
        return None  # already greeted: these tests focus on routing, not the welcome

    async def welcome(self, user_id, profile=None):
        return Response(text="привет", results=[])

    async def confirm(self, user_id, action, approved, profile=None):
        self.confirm_calls.append((user_id, action, approved))
        msg = "готово" if approved else "Отменено"
        return ActionResult(status=ResultStatus.done, action=action, message=msg)


def _channel(monkeypatch, *, token="123:ABC", voice=True, pipeline=None):
    _install_aiogram(monkeypatch)
    s = Settings()
    s.stt.enabled = voice
    s.channels = {"telegram": ChannelSettings(enabled=True, accept_voice=voice, token=token)}
    return TelegramChannel(s, pipeline or FakePipeline())


async def test_missing_token_raises_clear_error(monkeypatch):
    ch = _channel(monkeypatch, token=None)
    with pytest.raises(RuntimeError, match="Telegram не настроен"):
        await ch.start()


async def test_text_message_routes_and_renders(monkeypatch):
    ch = _channel(monkeypatch)
    await ch.start()
    on_text = FakeDispatcher.last.msg["text"]
    msg = FakeMessage(text="привет")
    await on_text(msg)
    assert msg.sent[0][0] == "hi"  # the aggregated response text was sent back


async def test_confirm_card_is_rendered_with_keyboard(monkeypatch):
    resp = Response(
        text="ok",
        results=[
            ActionResult(
                status=ResultStatus.needs_confirm,
                action="change_email",
                confirm_prompt="Поменять email на a@b.com?",
            )
        ],
    )
    ch = _channel(monkeypatch, pipeline=FakePipeline(resp))
    await ch.start()
    msg = FakeMessage(text="поменяй email")
    await FakeDispatcher.last.msg["text"](msg)
    # first the main text, then a confirm card carrying an inline keyboard
    assert msg.sent[0] == ("ok", None)
    prompt, markup = msg.sent[1]
    assert prompt == "Поменять email на a@b.com?"
    assert isinstance(markup, FakeKb)


async def test_voice_disabled_is_polite_and_skips_stt(monkeypatch):
    ch = _channel(monkeypatch, voice=False)
    await ch.start()
    on_voice = FakeDispatcher.last.msg["voice"]
    msg = FakeMessage(voice=pytypes.SimpleNamespace(file_id="v1"))
    await on_voice(msg)
    assert "текстом" in msg.sent[0][0]  # asked to type, STT never invoked


async def test_confirm_callback_parses_ok_and_cancel(monkeypatch):
    pipe = FakePipeline()
    ch = _channel(monkeypatch, pipeline=pipe)
    await ch.start()
    on_confirm = FakeDispatcher.last.cb

    ok = FakeQuery(data="ok:change_email")
    await on_confirm(ok)
    no = FakeQuery(data="no:change_email")
    await on_confirm(no)

    assert pipe.confirm_calls == [
        ("42", "change_email", True),
        ("42", "change_email", False),
    ]
    assert ok.answered and no.answered  # the callback query was acknowledged
    assert ok.message.sent[0][0] == "готово"
    assert no.message.sent[0][0] == "Отменено"
