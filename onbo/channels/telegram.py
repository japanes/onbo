"""Telegram adapter (aiogram): text + voice messages, inline confirm buttons."""
from __future__ import annotations

from ..core.schemas import ActionResult, ResultStatus
from .base import Channel


class TelegramChannel(Channel):
    name = "telegram"

    async def start(self) -> None:
        try:
            from aiogram import Bot, Dispatcher, F
            from aiogram.filters import CommandStart
            from aiogram.types import (
                CallbackQuery,
                InlineKeyboardButton,
                InlineKeyboardMarkup,
                Message,
            )
        except ImportError as exc:  # pragma: no cover - depends on the telegram extra
            raise RuntimeError("aiogram не установлен (extra `telegram`).") from exc

        cfg = self._channel_config()
        if not cfg or not cfg.token:
            raise RuntimeError(
                "Telegram не настроен: задайте channels.telegram.token "
                "(обычно через переменную окружения TELEGRAM_BOT_TOKEN в settings.yaml)."
            )
        bot = Bot(cfg.token)
        dp = Dispatcher()

        def confirm_keyboard(action: str) -> "InlineKeyboardMarkup":
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Ок", callback_data=f"ok:{action}"),
                        InlineKeyboardButton(text="Отмена", callback_data=f"no:{action}"),
                    ]
                ]
            )

        async def render(message: "Message", response: "Response") -> None:
            await message.answer(response.text)
            for result in response.results:
                if result.status == ResultStatus.needs_confirm and result.action:
                    await message.answer(
                        result.confirm_prompt or "Подтвердить?",
                        reply_markup=confirm_keyboard(result.action),
                    )

        async def greet_first_contact(message: "Message") -> None:
            """Auto-welcome on the user's first message (no-op afterwards)."""
            greeting = await self.pipeline.maybe_welcome(str(message.from_user.id))
            if greeting is not None:
                await render(message, greeting)

        @dp.message(CommandStart())
        async def on_start(message: "Message") -> None:
            # /start always (re)introduces the assistant.
            await render(message, await self.pipeline.welcome(str(message.from_user.id)))

        @dp.message(F.voice)
        async def on_voice(message: "Message") -> None:
            if not self.accepts_voice():
                await message.answer("Голосовой ввод выключен. Напишите, пожалуйста, текстом.")
                return
            await greet_first_contact(message)
            file = await bot.get_file(message.voice.file_id)
            buffer = await bot.download_file(file.file_path)
            text = await self.transcribe(buffer.read(), locale=message.from_user.language_code)
            await render(message, await self.handle_text(str(message.from_user.id), text))

        @dp.message(F.text)
        async def on_text(message: "Message") -> None:
            await greet_first_contact(message)
            await render(message, await self.handle_text(str(message.from_user.id), message.text))

        @dp.callback_query(F.data.startswith(("ok:", "no:")))
        async def on_confirm(query: "CallbackQuery") -> None:
            decision, action = query.data.split(":", 1)
            result: ActionResult = await self.pipeline.confirm(
                str(query.from_user.id), action, approved=decision == "ok"
            )
            await query.message.answer(result.message)
            await query.answer()

        await dp.start_polling(bot)
