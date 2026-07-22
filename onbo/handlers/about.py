"""about / capabilities: live introspection of what the assistant can do now.

Unlike the static docs indexed into the ``about`` collection, this reports the
current runtime state — enabled actions and their modes, voice flags — filtered
by the caller's role (same access rule as RAG).
"""
from __future__ import annotations

import os

from ..config import Settings
from ..core.schemas import ActionMode, ActionResult, Profile, ResultStatus
from .actions.registry import ActionSpec, spec_visible_to

# Reserved public collection for the assistant's own docs (docs/self/*.md).
# It carries no private content, so it is visible to every role.
ABOUT_COLLECTION = "about"

_MODE_HINT = {
    ActionMode.chat: "сразу",
    ActionMode.confirm: "с подтверждением",
    ActionMode.link: "по ссылке (чувствительные данные)",
}


class AboutHandler:
    def __init__(self, settings: Settings, actions: dict[str, ActionSpec]) -> None:
        self._settings = settings
        self._actions = actions

    async def answer(self, profile: Profile) -> ActionResult:
        lines = ["Я ассистент онбординга. Вот что умею прямо сейчас:", "", "Действия:"]
        # Only actions/pipelines available to the caller (same rule as the KB).
        for spec in self._actions.values():
            if spec_visible_to(spec, profile):
                lines.append(f"• {spec.description or spec.name} — {_MODE_HINT[spec.mode]}")

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
