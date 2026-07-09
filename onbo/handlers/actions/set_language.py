"""Low-risk action: switch UI language. mode: chat (executes immediately)."""
from __future__ import annotations

from ...core.schemas import ActionResult, Profile, ResultStatus
from .base import ActionHandler


class SetLanguage(ActionHandler):
    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        lang = entities.get("lang")
        # TODO: call the target product's API to persist the language for this user.
        return ActionResult(
            status=ResultStatus.done,
            action="set_language",
            message=f"Язык интерфейса переключён на «{lang}».",
        )


handler = SetLanguage()
