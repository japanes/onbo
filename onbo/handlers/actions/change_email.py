"""Reversible-but-important action: change email. mode: confirm (Ok/Cancel first).

Custom validation (email shape) lives here; the actual backend call is the
shared, config-driven one from http_action (driven by the `api:` block).
"""
from __future__ import annotations

from ...core.schemas import ActionResult, Profile
from .base import ActionHandler
from .http_action import call_product_api


class ChangeEmail(ActionHandler):
    async def validate(self, entities: dict) -> dict:
        new_email = (entities.get("new_email") or "").strip()
        if "@" not in new_email:
            raise ValueError("Нужен корректный email в формате name@example.com.")
        entities["new_email"] = new_email
        return entities

    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        return await call_product_api(self.spec, profile, entities)


handler = ChangeEmail()
