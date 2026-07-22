"""Proactive welcome: a first-contact digest tailored to the user's access.

On a user's first message (or an explicit ``/welcome``) the assistant introduces
what *this* user can do — actions and pipelines visible to their role/department
(same access rule as the KB), the knowledge they can ask about, and an optional
role starter video. The raw facts are optionally smoothed by the LLM; with no
model reachable it falls back to the plain template, so the welcome always works.
"""
from __future__ import annotations

from ..config import Settings
from ..core.llm import LLM
from ..core.schemas import ActionMode, ActionResult, Profile, ResultStatus
from .about import _MODE_HINT
from .actions.registry import spec_visible_to
from .media import media_url

# Present immediate actions first, then confirmed ones, then sensitive links.
_MODE_ORDER = (ActionMode.chat, ActionMode.confirm, ActionMode.link)


class WelcomeHandler:
    def __init__(
        self,
        settings: Settings,
        specs: dict,
        kb_admin,
        llm: LLM | None = None,
    ) -> None:
        self._settings = settings
        self._specs = specs        # merged actions + pipelines (shared namespace)
        self._kb = kb_admin
        self._llm = llm

    async def answer(self, profile: Profile) -> ActionResult:
        video_line = self._video_line(profile)

        override = self._text_override(profile)
        if override is not None:
            body = override
        else:
            facts = self._facts(profile)
            body = await self._smooth(facts)

        message = f"{body}\n\n{video_line}" if video_line else body
        return ActionResult(status=ResultStatus.answer, message=message)

    # -- digest assembly ------------------------------------------------------

    def _facts(self, profile: Profile) -> list[str]:
        dept = profile.department or "без отдела"
        roles = ", ".join(profile.roles) or "—"
        lines = [
            "Привет! Я ассистент онбординга — помогу освоиться и возьму на себя рутину.",
            f"По нашим данным вы из отдела «{dept}», роли: {roles}. "
            "Показываю только то, что доступно именно вам.",
        ]

        actions = self._actions_block(profile)
        if actions:
            lines += ["", "Что можно сделать прямо здесь:", *actions]

        kb = self._kb_block(profile)
        if kb:
            lines += ["", "О чём можно спросить:", *kb]

        lines += ["", "Просто напишите, что нужно — я подскажу или сделаю."]
        return lines

    def _actions_block(self, profile: Profile) -> list[str]:
        """Actions and pipelines visible to the user, ordered by mode."""
        visible = [s for s in self._specs.values() if spec_visible_to(s, profile)]
        out: list[str] = []
        for mode in _MODE_ORDER:
            for spec in (s for s in visible if s.mode == mode):
                out.append(f"• {spec.description or spec.name} — {_MODE_HINT[mode]}")
        return out

    def _kb_block(self, profile: Profile) -> list[str]:
        """Accessible KB sections + a few sample questions (empty without a DB)."""
        try:
            qa = self._kb.list_qa()
        except Exception:
            qa = []
        visible = [q for q in qa if self._qa_visible(q, profile)]
        if not visible:
            return []
        collections = sorted({q["collection"] for q in visible})
        out = [f"Доступные разделы базы знаний: {', '.join(collections)}."]
        out += [f"  — {q['question']}" for q in visible[:5]]
        return out

    @staticmethod
    def _qa_visible(qa: dict, profile: Profile) -> bool:
        # Same rule as spec_visible_to / the KB access filter, on a list_qa() row.
        department = qa.get("department")
        roles = qa.get("roles") or []
        dept_ok = department is None or department == profile.department
        roles_ok = not roles or bool(set(roles) & set(profile.roles or []))
        return dept_ok and roles_ok

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
        """Rewrite the raw facts warmly via the LLM; plain template if unavailable."""
        plain = "\n".join(facts)
        if self._llm is None:
            return plain
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты — дружелюбный ассистент онбординга. Перепиши приветствие для "
                    "нового сотрудника: тепло, живо и коротко, простыми словами. "
                    "СОХРАНИ все факты, названия действий и разделов без изменений, "
                    "ничего не выдумывай и не добавляй ссылок. Ответь только текстом "
                    "приветствия на русском."
                ),
            },
            {"role": "user", "content": plain},
        ]
        try:
            text = (await self._llm.complete(messages, temperature=0.3)).strip()
        except Exception:  # litellm missing or model unreachable -> template
            return plain
        return text or plain
