"""Reversible-but-important action: change email. mode: confirm (Ok/Cancel first)."""
from __future__ import annotations

from ...core.schemas import ActionResult, Profile, ResultStatus
from .base import ActionHandler


class ChangeEmail(ActionHandler):
    async def validate(self, entities: dict) -> dict:
        new_email = (entities.get("new_email") or "").strip()
        if "@" not in new_email:
            raise ValueError("Нужен корректный email в формате name@example.com.")
        entities["new_email"] = new_email
        return entities

    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        new_email = entities.get("new_email")
        # TODO: call the target product's API to change the email for this user.
        return ActionResult(
            status=ResultStatus.done,
            action="change_email",
            message=f"Email изменён на {new_email}.",
        )


handler = ChangeEmail()
