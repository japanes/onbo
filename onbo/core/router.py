"""Route a single classified action to the right handler / mode."""
from __future__ import annotations

from ..handlers.actions.lookup import resolve_lookups
from .schemas import (
    ActionMode,
    ActionResult,
    ActionType,
    ClassifiedAction,
    Link,
    Profile,
    ResultStatus,
)


class _Blanks(dict):
    """Renders a placeholder nothing filled as «…» instead of crashing."""

    def __missing__(self, key: str) -> str:
        return "…"


def missing_params(spec, entities: dict) -> list[str]:
    """Required parameters this action still has no value for.

    Presence of the key is not enough: entities arrive from a language model,
    and "I don't know" comes back as an empty value at least as often as an
    absent key. Both mean the same thing — ask the person.
    """
    return [
        name
        for name, param in spec.params.items()
        if param.required and not str(entities.get(name, "")).strip()
    ]


def ask_for(spec, missing: list[str]) -> str:
    """The question a person can answer, built from the parameter descriptions."""
    wanted = "; ".join(spec.params[name].label(name) for name in missing)
    return f"Чтобы «{spec.description}», уточните: {wanted}."


class Router:
    def __init__(self, actions, registry, rag_handler, about_handler, session) -> None:
        self._actions = actions          # name -> ActionSpec
        self._registry = registry        # ActionRegistry (name -> handler instance)
        self._rag = rag_handler
        self._about = about_handler
        self._session = session

    async def route(self, action: ClassifiedAction, profile: Profile) -> ActionResult:
        if action.type == ActionType.about:
            return await self._about.answer(profile)
        if action.type == ActionType.rag_query:
            return await self._rag.answer(action.query or "", profile)
        if action.type == ActionType.profile_action:
            return await self._profile_action(action, profile)
        return ActionResult(status=ResultStatus.failed, message="Не понял запрос, переформулируйте.")

    async def _profile_action(self, action: ClassifiedAction, profile: Profile) -> ActionResult:
        spec = self._actions.get(action.action or "")
        if spec is None:
            return ActionResult(
                status=ResultStatus.failed,
                action=action.action,
                message=f"Действие «{action.action}» не поддерживается.",
            )

        # Sensitive data is never touched in chat — hand out a link and stop.
        if spec.sensitive or spec.mode == ActionMode.link:
            # The address also travels as a link (schemas.Link), so a channel
            # renders it the way it renders the links of a knowledge-base answer —
            # a button — instead of leaving a bare URL sitting in the sentence.
            return ActionResult(
                status=ResultStatus.link,
                action=spec.name,
                link_url=spec.link_url,
                message=f"{spec.description}: откройте страницу по ссылке.",
                links=[Link(title=spec.description, url=spec.link_url)] if spec.link_url else [],
            )

        # Words the product's own directories have to be consulted about
        # («инстаграм» -> platform id 3) are resolved first: after this every
        # value in `entities` is one the API will actually accept, so the checks
        # below, the confirmation text and the call itself all see the same thing.
        found = await resolve_lookups(spec, action.entities, profile)
        action.entities = found.entities
        if found.error:
            return ActionResult(status=ResultStatus.failed, action=spec.name, message=found.error)
        if found.question:
            await self._session.park_input(
                profile.user_id, spec.name, action.entities, [found.asked] if found.asked else None
            )
            return ActionResult(
                status=ResultStatus.needs_input, action=spec.name, message=found.question
            )

        # Validate / slot-fill required params before doing anything else.
        missing = missing_params(spec, action.entities)
        if missing:
            # Park what we already have: the next message is read as the answer
            # to this question, so the person fills the gaps in the chat window
            # instead of being told to start over.
            await self._session.park_input(profile.user_id, spec.name, action.entities)
            return ActionResult(
                status=ResultStatus.needs_input,
                action=spec.name,
                message=ask_for(spec, missing),
            )

        if spec.mode == ActionMode.confirm:
            template = spec.confirm_prompt or f"Подтвердите: {spec.description}?"
            prompt = template.format_map(_Blanks(action.entities))
            # Park the pending action; it executes only when the user confirms.
            await self._session.park(profile.user_id, spec.name, action.entities)
            return ActionResult(status=ResultStatus.needs_confirm, action=spec.name, confirm_prompt=prompt)

        # mode == chat → execute immediately
        handler = self._registry.get(spec.name)
        if handler is None:
            return ActionResult(status=ResultStatus.failed, action=spec.name, message="Обработчик не найден.")
        entities = await handler.validate(action.entities)
        return await handler.execute(profile, entities)
