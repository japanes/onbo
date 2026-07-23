"""Proactive welcome: three lines on first contact, and nothing else.

On a user's first message (or an explicit ``/welcome``) the assistant says who
it is and invites the person to write what they need — plus an optional starter
video for their role. What *this* user may do is a separate question, answered
on request by :mod:`onbo.handlers.about`.

That split is the whole point of this file. The greeting used to enumerate every
visible action, load the knowledge base to sample it, and then hand the whole
wall to the LLM asking it to rewrite it *preserving every fact* — so the model
had to regenerate forty lines before the person saw a word, on the one message
where waiting is most visible. Now the greeting reads nothing and, by default,
calls no model at all (``welcome.smooth``).
"""
from __future__ import annotations

from ..config import Settings
from ..core.llm import LLM
from ..core.schemas import ActionResult, Profile, ResultStatus
from .media import media_url

# The greeting itself. Fixed text, on purpose: it is read once, it must appear
# instantly, and the third line is what makes the short version sufficient —
# the full list is one question away.
_GREETING = (
    "Привет! Я ассистент онбординга — помогу освоиться и возьму на себя рутину.",
    "Напишите своими словами, что нужно: отвечу на вопрос или сделаю сам.",
    "Спросите «что ты умеешь» — покажу список того, что доступно вам.",
)


class WelcomeHandler:
    def __init__(self, settings: Settings, llm: LLM | None = None) -> None:
        self._settings = settings
        self._llm = llm

    async def answer(self, profile: Profile) -> ActionResult:
        video_line = self._video_line(profile)

        override = self._text_override(profile)
        if override is not None:
            body = override
        elif self._settings.welcome.smooth:
            body = await self._smooth(list(_GREETING))
        else:
            body = "\n".join(_GREETING)

        message = f"{body}\n\n{video_line}" if video_line else body
        return ActionResult(status=ResultStatus.answer, message=message)

    def _video_line(self, profile: Profile) -> str:
        key = self._audience_key(self._settings.welcome.video, profile)
        if key is None:
            return ""
        return f"Видео-знакомство: {media_url(self._settings, self._settings.welcome.video[key])}"

    def _text_override(self, profile: Profile) -> str | None:
        key = self._audience_key(self._settings.welcome.text_overrides, profile)
        return None if key is None else self._settings.welcome.text_overrides[key]

    @staticmethod
    def _audience_key(mapping: dict[str, str], profile: Profile) -> str | None:
        """First matching key in a department|role -> value map (department wins)."""
        if not mapping:
            return None
        if profile.department and profile.department in mapping:
            return profile.department
        for role in profile.roles or []:
            if role in mapping:
                return role
        return None

    async def _smooth(self, facts: list[str]) -> str:
        """Rewrite the greeting warmly via the LLM; plain template if unavailable.

        Off by default. Three lines are cheap to rewrite — but they are also
        already short and already friendly, so this buys wording, not content.
        """
        plain = "\n".join(facts)
        if self._llm is None:
            return plain
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты — дружелюбный ассистент онбординга. Перепиши приветствие для "
                    "нового сотрудника: тепло, живо и коротко, простыми словами, "
                    "не длиннее трёх предложений. СОХРАНИ приглашение написать, что "
                    "нужно, и подсказку спросить «что ты умеешь». Ничего не выдумывай "
                    "и не добавляй ссылок. Ответь только текстом приветствия на русском."
                ),
            },
            {"role": "user", "content": plain},
        ]
        try:
            text = (await self._llm.complete(messages)).strip()
        except Exception:  # litellm missing or model unreachable -> template
            return plain
        return text or plain
