"""Pipeline: wires the stages together and orchestrates one request."""
from __future__ import annotations

from ..auth.profiles import resolve_profile
from ..config import Settings, load_settings
from ..handlers.about import AboutHandler
from ..handlers.actions.registry import (
    ActionRegistry,
    load_action_specs,
    load_pipeline_specs,
)
from ..handlers.rag import RagHandler
from ..handlers.welcome import WelcomeHandler
from ..kb.admin import KnowledgeBaseAdmin
from ..state import welcome as welcome_state
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
        self.pipelines = load_pipeline_specs(self.actions)
        # Pipelines route like any other action: one shared name namespace so the
        # classifier catalog, router and about self-doc see them without branching.
        self.specs = {**self.actions, **self.pipelines}
        self.registry = ActionRegistry(self.specs, self.pipelines)
        self.session = Session(self.settings)
        self.llm = LLM(self.settings)
        self.classifier = Classifier(
            self.llm,
            self.specs,
            actions_enabled=self.settings.features.actions,
            rag_enabled=self.settings.features.rag,
        )
        self.rag = RagHandler(self.settings)
        self.about = AboutHandler(self.settings, self.specs)
        self.welcome_handler = WelcomeHandler(
            self.settings, self.specs, KnowledgeBaseAdmin(self.settings), self.llm
        )
        self.router = Router(self.specs, self.registry, self.rag, self.about, self.session)

    async def handle(self, env: Envelope) -> Response:
        """Full path: auth -> classify -> route each action -> aggregate."""
        profile = await resolve_profile(env.user_id, self.settings)
        classification = await self.classifier.classify(env, profile)
        results = [await self.router.route(action, profile) for action in classification.actions]
        return aggregator.aggregate(results)

    async def welcome(self, user_id: str) -> Response:
        """Proactive first-contact digest, tailored to the user's access.

        Explicit trigger (/welcome, /start): always builds the digest and marks
        the user as greeted so the auto-welcome doesn't fire again.
        """
        profile = await resolve_profile(user_id, self.settings)
        result = await self.welcome_handler.answer(profile)
        await welcome_state.mark_welcomed(user_id, self.settings, self.session)
        return aggregator.aggregate([result])

    async def maybe_welcome(self, user_id: str) -> Response | None:
        """Auto-welcome on first contact: only if enabled and not greeted yet."""
        if not (self.settings.features.welcome and self.settings.welcome.enabled):
            return None
        if await welcome_state.is_welcomed(user_id, self.settings, self.session):
            return None
        return await self.welcome(user_id)

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
