"""Action plugin interface.

One file per action under handlers/actions/, each exposing a module-level
``handler`` instance. The router calls ``validate`` (check / slot-fill), then
``execute`` — but only for ``mode: chat`` immediately, and for ``mode: confirm``
only after the user presses Ok. ``mode: link`` never reaches a handler.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ...core.schemas import ActionResult, Profile


class ActionHandler(ABC):
    # Populated by the registry with the action's ActionSpec (api block, messages).
    spec = None

    async def validate(self, entities: dict) -> dict:
        """Check / normalise / slot-fill entities. Return the entities to execute with.

        Raise ValueError with a user-facing message if the input is invalid.
        """
        return entities

    @abstractmethod
    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        """Perform the action against the target product's API."""
        raise NotImplementedError
