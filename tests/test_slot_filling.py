"""Asking for a missing parameter, and reading the answer to that question.

An action that needs details the message never mentioned used to be a dead end:
onbo replied «не хватает: project_id» and the person's answer was classified
from scratch as a knowledge-base question. These tests cover the loop that
replaces it — ask in words the person can answer, remember the half-filled
action, and let the next message complete it.
"""
from __future__ import annotations

from onbo.core.classifier import Classifier
from onbo.core.pipeline import Pipeline
from onbo.core.router import Router
from onbo.core.schemas import (
    ActionMode,
    ActionResult,
    ActionType,
    Classification,
    ClassifiedAction,
    Envelope,
    ResultStatus,
)
from onbo.handlers.actions.registry import ActionSpec, ParamSpec
from tests.conftest import FakeRegistry, FakeSession, RecordingHandler

SPECS = {
    "create_post": ActionSpec(
        name="create_post",
        description="Создать пост",
        mode=ActionMode.confirm,
        confirm_prompt="Создать пост «{topic_title}» в проекте #{project_id}?",
        params={
            "project_id": ParamSpec(required=True, description="в каком проекте"),
            "topic_title": ParamSpec(description="заголовок поста"),
        },
    )
}


class _PartialLLM:
    """A model that hears the topic but not the project — the usual case.

    It answers the classification call and refuses the follow-up extraction, so
    the reply to «в каком проекте» is filled by the cheap path alone.
    """

    async def structured(self, messages, schema):
        if schema is Classification:
            return Classification(
                actions=[
                    ClassifiedAction(
                        type=ActionType.profile_action,
                        action="create_post",
                        entities={"topic_title": "про арбузы", "project_id": None},
                        confidence=0.9,
                    )
                ]
            )
        raise RuntimeError("no LLM")


class _FakeRag:
    async def answer(self, query, profile):
        return ActionResult(status=ResultStatus.answer, message=f"rag:{query}")


class _TestPipeline(Pipeline):
    """The real pipeline wiring, minus the backends it would otherwise open."""

    def __init__(self, handler=None, llm=None) -> None:
        self.specs = SPECS
        self.session = FakeSession()
        self.classifier = Classifier(llm or _PartialLLM(), SPECS)
        self.router = Router(
            SPECS, FakeRegistry(handler or RecordingHandler()), _FakeRag(), None, self.session
        )


async def _say(pipeline, profile, text):
    return await pipeline.handle(Envelope(user_id=profile.user_id, channel="web", text=text), profile)


async def test_the_answer_to_the_question_completes_the_action(profile):
    pipeline = _TestPipeline()

    asked = await _say(pipeline, profile, "создай пост про арбузы")
    assert asked.results[0].status == ResultStatus.needs_input
    assert "в каком проекте" in asked.results[0].message

    answered = await _say(pipeline, profile, "12")
    assert answered.results[0].status == ResultStatus.needs_confirm
    assert answered.results[0].confirm_prompt == "Создать пост «про арбузы» в проекте #12?"


async def test_a_person_who_changed_their_mind_is_not_stuck_in_the_form(profile):
    """An unrelated message is handled as itself, and the form is dropped."""
    pipeline = _TestPipeline()
    await pipeline.session.park_input(profile.user_id, "create_post", {})

    class _OnlyRag:
        async def structured(self, messages, schema):
            raise RuntimeError("no LLM")   # falls back to a rag_query

    pipeline.classifier = Classifier(_OnlyRag(), SPECS)
    answer = await _say(pipeline, profile, "а как оформить отпуск?")

    assert answer.results[0].message == "rag:а как оформить отпуск?"
    assert profile.user_id not in pipeline.session.awaiting


async def test_nothing_parked_means_the_message_is_classified_as_usual(profile):
    pipeline = _TestPipeline()
    assert await pipeline._resume_pending(
        Envelope(user_id=profile.user_id, channel="web", text="12"), profile
    ) == (None, None)


async def test_a_forgotten_action_name_does_not_break_the_reply(profile):
    """The registry can change under a parked action (config reload, redeploy)."""
    pipeline = _TestPipeline()
    await pipeline.session.park_input(profile.user_id, "action_that_no_longer_exists", {})
    assert await pipeline._resume_pending(
        Envelope(user_id=profile.user_id, channel="web", text="12"), profile
    ) == (None, None)
