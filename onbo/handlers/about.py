"""about / capabilities: live introspection of what the assistant can do now.

Unlike the static docs indexed into the ``about`` collection, this reports the
current runtime state — enabled actions and their modes, knowledge-base sections,
voice flags — filtered by the caller's role (same access rule as RAG).

This is also where the command list lives: the greeting deliberately does not
enumerate anything (see :mod:`onbo.handlers.welcome`), it points here. Nothing
in this file calls an LLM, so «что ты умеешь» answers at the speed of a
dictionary lookup plus one query.
"""
from __future__ import annotations

import os

from ..config import Settings
from ..core.schemas import ActionMode, ActionResult, Profile, ResultStatus
from .actions.registry import ActionSpec, spec_visible_to

# Reserved public collection for the assistant's own docs (docs/self/*.md).
# It carries no private content, so it is visible to every role.
ABOUT_COLLECTION = "about"

# Group by what happens when you ask, not by name: the one thing a person needs
# to know before reading the list is whether these things fire on their own.
_MODE_TITLE = {
    ActionMode.chat: "Сделаю сразу:",
    ActionMode.confirm: "Сделаю после вашего подтверждения:",
    ActionMode.link: "Отдам ссылкой — чувствительные данные:",
}
_MODE_ORDER = (ActionMode.chat, ActionMode.confirm, ActionMode.link)


class AboutHandler:
    def __init__(
        self,
        settings: Settings,
        actions: dict[str, ActionSpec],
        kb_admin=None,
    ) -> None:
        self._settings = settings
        self._actions = actions
        self._kb = kb_admin

    async def answer(self, profile: Profile) -> ActionResult:
        lines = ["Я ассистент онбординга. Вот что умею прямо сейчас."]
        lines += self._actions_block(profile)
        lines += self._kb_block(profile)

        stt = "включён" if self._settings.stt.enabled else "выключен"
        tts = "включена" if self._settings.tts.enabled else "выключена (голос только на вход)"
        channels = ", ".join(name for name, ch in self._settings.channels.items() if ch.enabled) or "—"
        dept = profile.department or "без отдела"
        roles = ", ".join(profile.roles) or "—"

        lines += [
            "",
            f"Каналы: {channels}.",
            f"Голосовой ввод (STT): {stt}. Озвучка ответов (TTS): {tts}.",
            f"Ваш доступ к базе знаний: отдел «{dept}», роли: {roles}.",
        ]
        return ActionResult(status=ResultStatus.answer, message="\n".join(lines))

    def _actions_block(self, profile: Profile) -> list[str]:
        """Actions and pipelines available to the caller, grouped by mode.

        Same access rule as the KB — a person is never shown a command they
        would be refused, because a list of things you cannot have reads as a
        broken assistant rather than as a permission boundary.
        """
        visible = [s for s in self._actions.values() if spec_visible_to(s, profile)]
        if not visible:
            return ["", "Действий, доступных вам, сейчас нет."]
        out = ["", f"Действия, доступные вам ({len(visible)}):"]
        for mode in _MODE_ORDER:
            group = [s for s in visible if s.mode == mode]
            if not group:
                continue
            out.append(f"  {_MODE_TITLE[mode]}")
            out += [f"    • {spec.description or spec.name}" for spec in group]
        return out

    def _kb_block(self, profile: Profile) -> list[str]:
        """Which knowledge-base sections this person may ask about.

        One query over the collections table, not over the pairs: naming the
        sections is what a person needs here, and listing sample questions used
        to mean loading every Q&A row in the base to print five of them.
        """
        if self._kb is None:
            return []
        try:
            collections = self._kb.list_collections()
        except Exception:  # no database configured -> the rest of about still works
            return []
        names = sorted(c["name"] for c in collections if _row_visible(c, profile))
        if not names:
            return []
        return ["", f"Разделы базы знаний, о которых можно спросить: {', '.join(names)}."]


def _row_visible(row: dict, profile: Profile) -> bool:
    """``spec_visible_to`` for a plain dict row (a collection out of the admin API).

    Kept separate on purpose: ``spec_visible_to`` reads attributes, and a dict
    has none — it would silently see every row as public.
    """
    department = row.get("department")
    roles = row.get("roles") or []
    dept_ok = department is None or department == profile.department
    roles_ok = not roles or bool(set(roles) & set(profile.roles or []))
    return dept_ok and roles_ok


async def index_self_docs(settings: Settings) -> int:
    """Index the bundled docs/self/*.md into the public `about` collection.

    Backs ``onbo about`` and gives the assistant a from-the-box demo: questions
    like "как тебя настроить?" then go through the normal RAG path.
    """
    from ..kb.sources.files import FileSource
    from ..kb.index import Indexer

    docs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "docs", "self")
    if not os.path.isdir(docs_dir):
        return 0
    docs = FileSource(docs_dir).fetch()
    # No access tags -> public, readable by every role.
    return await Indexer(settings).index_documents(docs, ABOUT_COLLECTION)
