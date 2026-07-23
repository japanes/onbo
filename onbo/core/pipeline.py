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
from .router import Router, missing_params
from .schemas import (
    ActionResult,
    ActionType,
    ClassifiedAction,
    Envelope,
    Profile,
    Response,
    ResultStatus,
)


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

    async def handle(self, env: Envelope, profile: Profile | None = None) -> Response:
        """Full path: auth -> classify -> route each action -> aggregate.

        ``profile`` is passed in when the channel already authenticated the caller
        (a signed token carrying department and roles); otherwise it is looked up
        in the users table. Either way it is the only source of the access filter.
        """
        profile = profile or await resolve_profile(env.user_id, self.settings)
        resumed = await self._resume_pending(env, profile)
        if resumed is not None:
            return aggregator.aggregate([resumed])
        classification = await self.classifier.classify(env, profile)
        results = [await self.router.route(action, profile) for action in classification.actions]
        return aggregator.aggregate(results)

    async def _resume_pending(self, env: Envelope, profile: Profile) -> ActionResult | None:
        """Read this message as the answer to the question we last asked.

        An action that stopped for a missing parameter is parked; the reply to
        «уточните: в каком проекте» is usually a fragment («в 12-м»), which the
        classifier alone would turn into a knowledge-base question. So the
        parked action gets first refusal on the message.

        Returns ``None`` — and the message is classified normally — when nothing
        is parked or the reply filled nothing in. That is what keeps a person
        who changed their mind from being held inside a half-finished form.
        """
        pending = await self.session.pop_input(profile.user_id)
        if not pending:
            return None
        spec = self.specs.get(pending.get("action") or "")
        if spec is None:
            return None
        entities = dict(pending.get("entities") or {})
        # What the question was about, when it said so — otherwise the usual
        # "required and still empty". See SessionStore.park_input.
        missing = list(pending.get("wanted") or []) or missing_params(spec, entities)
        filled = (
            await self.classifier.fill(spec, missing, env.text, env.ts) if missing else {}
        )
        if not filled:
            return None
        action = ClassifiedAction(
            type=ActionType.profile_action,
            action=spec.name,
            entities={**entities, **filled},
            confidence=1.0,
        )
        return await self.router.route(action, profile)

    async def welcome(self, user_id: str, profile: Profile | None = None) -> Response:
        """Proactive first-contact digest, tailored to the user's access.

        Explicit trigger (/welcome, /start): always builds the digest and marks
        the user as greeted so the auto-welcome doesn't fire again.
        """
        profile = profile or await resolve_profile(user_id, self.settings)
        result = await self.welcome_handler.answer(profile)
        await welcome_state.mark_welcomed(user_id, self.settings, self.session)
        return aggregator.aggregate([result])

    async def maybe_welcome(
        self, user_id: str, profile: Profile | None = None
    ) -> Response | None:
        """Auto-welcome on first contact: only if enabled and not greeted yet."""
        if not (self.settings.features.welcome and self.settings.welcome.enabled):
            return None
        if await welcome_state.is_welcomed(user_id, self.settings, self.session):
            return None
        return await self.welcome(user_id, profile)

    async def confirm(
        self,
        user_id: str,
        action_name: str,
        approved: bool,
        profile: Profile | None = None,
    ) -> ActionResult:
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
        profile = profile or await resolve_profile(user_id, self.settings)
        handler = self.registry.get(action_name)
        if handler is None:
            return ActionResult(status=ResultStatus.failed, action=action_name, message="Обработчик не найден.")
        return await handler.execute(profile, entities)
