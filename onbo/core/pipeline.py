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
from ..rag.retriever import Retriever
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
        # One retriever for both readers of the index: the knowledge base and the
        # command shortlist are the same collection with two kinds (rag/store.py),
        # and a second instance would load a second copy of the embedding model.
        self.retriever = Retriever(self.settings)
        self.classifier = Classifier(
            self.llm,
            self.specs,
            actions_enabled=self.settings.features.actions,
            rag_enabled=self.settings.features.rag,
            retriever=self.retriever,
            shortlist_size=self.settings.actions.shortlist_size,
        )
        self.rag = RagHandler(self.settings, self.retriever)
        # The command list and the KB sections live in `about` (asked for), not in
        # the greeting (unasked for) — hence the KB admin goes here.
        self.about = AboutHandler(self.settings, self.specs, KnowledgeBaseAdmin(self.settings))
        self.welcome_handler = WelcomeHandler(self.settings, self.llm)
        self.router = Router(self.specs, self.registry, self.rag, self.about, self.session)

    async def ensure_action_index(self) -> int:
        """Re-embed the command catalogue if actions.yaml has moved on.

        Called once at startup. Editing actions.yaml and restarting is the whole
        deployment procedure for a new command, so noticing the change here is
        what keeps «я добавил команду, а он её не видит» from being a bug report.

        Never raises: without an index the classifier prints the full catalogue —
        slower and dearer, still correct. Refusing to boot over it would not be.
        """
        if not (self.settings.actions.autoindex and self.settings.features.actions):
            return 0
        from ..handlers.actions.index import reindex_if_stale

        try:
            return await reindex_if_stale(self.settings, self.specs)
        except Exception:  # noqa: BLE001 - Qdrant down, embeddings extra missing, ...
            return 0

    async def handle(self, env: Envelope, profile: Profile | None = None) -> Response:
        """Full path: auth -> classify -> route each action -> aggregate.

        ``profile`` is passed in when the channel already authenticated the caller
        (a signed token carrying department and roles); otherwise it is looked up
        in the users table. Either way it is the only source of the access filter.
        """
        profile = profile or await resolve_profile(env.user_id, self.settings)
        resumed, parked = await self._resume_pending(env, profile)
        if resumed is not None:
            return aggregator.aggregate([resumed])
        # The parked name survives a failed resume on purpose: the reply that did
        # not fill the form is still about that command far more often than not,
        # and the shortlist would otherwise never surface it — "ещё раз" looks
        # like nothing in the catalogue.
        classification = await self.classifier.classify(env, profile, parked)
        results = [await self.router.route(action, profile) for action in classification.actions]
        return aggregator.aggregate(results)

    async def _resume_pending(
        self, env: Envelope, profile: Profile
    ) -> tuple[ActionResult | None, str | None]:
        """Read this message as the answer to the question we last asked.

        An action that stopped for a missing parameter is parked; the reply to
        «уточните: в каком проекте» is usually a fragment («в 12-м»), which the
        classifier alone would turn into a knowledge-base question. So the
        parked action gets first refusal on the message.

        Returns ``(None, name)`` — and the message is classified normally — when
        the reply filled nothing in. That is what keeps a person who changed
        their mind from being held inside a half-finished form; the name is
        handed on so the classifier can still consider that command.
        """
        pending = await self.session.pop_input(profile.user_id)
        if not pending:
            return None, None
        spec = self.specs.get(pending.get("action") or "")
        if spec is None:
            return None, None
        entities = dict(pending.get("entities") or {})
        # What the question was about, when it said so — otherwise the usual
        # "required and still empty". See SessionStore.park_input.
        missing = list(pending.get("wanted") or []) or missing_params(spec, entities)
        filled = (
            await self.classifier.fill(spec, missing, env.text, env.ts) if missing else {}
        )
        if not filled:
            return None, spec.name
        action = ClassifiedAction(
            type=ActionType.profile_action,
            action=spec.name,
            entities={**entities, **filled},
            confidence=1.0,
        )
        return await self.router.route(action, profile), spec.name

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
