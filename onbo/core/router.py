"""Route a single classified action to the right handler / mode."""
from __future__ import annotations

from .schemas import (
    ActionMode,
    ActionResult,
    ActionType,
    ClassifiedAction,
    Profile,
    ResultStatus,
)


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
            return ActionResult(
                status=ResultStatus.link,
                action=spec.name,
                link_url=spec.link_url,
                message=f"{spec.description}: откройте страницу по ссылке.",
            )

        # Validate / slot-fill required params before doing anything else.
        missing = [name for name, ps in spec.params.items() if ps.required and name not in action.entities]
        if missing:
            return ActionResult(
                status=ResultStatus.needs_input,
                action=spec.name,
                message=f"Для «{spec.description}» не хватает: {', '.join(missing)}.",
            )

        if spec.mode == ActionMode.confirm:
            prompt = (spec.confirm_prompt or f"Подтвердите: {spec.description}?").format(**action.entities)
            # Park the pending action; it executes only when the user confirms.
            await self._session.park(profile.user_id, spec.name, action.entities)
            return ActionResult(status=ResultStatus.needs_confirm, action=spec.name, confirm_prompt=prompt)

        # mode == chat → execute immediately
        handler = self._registry.get(spec.name)
        if handler is None:
            return ActionResult(status=ResultStatus.failed, action=spec.name, message="Обработчик не найден.")
        entities = await handler.validate(action.entities)
        return await handler.execute(profile, entities)
