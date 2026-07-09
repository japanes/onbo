"""Pipeline: wires the stages together and orchestrates one request."""
from __future__ import annotations

from ..auth.profiles import resolve_profile
from ..config import Settings, load_settings
from ..handlers.about import AboutHandler
from ..handlers.actions.registry import ActionRegistry, load_action_specs
from ..handlers.rag import RagHandler
from ..state.session import Session
from . import aggregator
from .classifier import Classifier
from .llm import LLM
from .router import Router
from .schemas import ActionResult, Envelope, Response, ResultStatus


class Pipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.actions = load_action_specs()
        self.registry = ActionRegistry(self.actions)
        self.session = Session(self.settings)
        self.llm = LLM(self.settings)
        self.classifier = Classifier(self.llm, self.actions)
        self.rag = RagHandler(self.settings)
        self.about = AboutHandler(self.settings, self.actions)
        self.router = Router(self.actions, self.registry, self.rag, self.about, self.session)

    async def handle(self, env: Envelope) -> Response:
        """Full path: auth -> classify -> route each action -> aggregate."""
        profile = await resolve_profile(env.user_id, self.settings)
        classification = await self.classifier.classify(env, profile)
        results = [await self.router.route(action, profile) for action in classification.actions]
        return aggregator.aggregate(results)

    async def confirm(self, user_id: str, action_name: str, approved: bool) -> ActionResult:
        """Resolve a parked confirm action once the user presses Ok/Cancel."""
        entities = await self.session.pop(user_id, action_name)
        if entities is None:
            return ActionResult(
                status=ResultStatus.failed,
                action=action_name,
                message="Нет действия, ожидающего подтверждения.",
            )
        if not approved:
            return ActionResult(status=ResultStatus.done, action=action_name, message="Отменено.")
        profile = await resolve_profile(user_id, self.settings)
        handler = self.registry.get(action_name)
        if handler is None:
            return ActionResult(status=ResultStatus.failed, action=action_name, message="Обработчик не найден.")
        return await handler.execute(profile, entities)
