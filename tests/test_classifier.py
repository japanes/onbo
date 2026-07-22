"""Classifier offline fallback + entity backfill (no LLM configured)."""
from __future__ import annotations

import pytest

from onbo.core.classifier import Classifier
from onbo.core.schemas import (
    ActionType,
    Classification,
    ClassifiedAction,
    Envelope,
    Profile,
)
from onbo.handlers.actions.registry import ActionSpec, ParamSpec


def _actions():
    return {
        "set_language": ActionSpec(
            name="set_language",
            description="Сменить язык интерфейса",
            params={"lang": ParamSpec(type="enum", required=True, values=["ru", "en"])},
        ),
        "change_email": ActionSpec(
            name="change_email",
            description="Сменить email",
            params={"new_email": ParamSpec(type="email", required=True)},
        ),
    }


class BrokenLLM:
    """Stands in for an unconfigured/unreachable LLM to force the fallback."""

    async def structured(self, messages, schema):
        raise RuntimeError("no LLM")


def _classifier():
    return Classifier(BrokenLLM(), _actions())


@pytest.fixture
def profile():
    return Profile(user_id="u1", department="accounting", roles=["accountant"])


async def test_fallback_extracts_email(profile):
    env = Envelope(user_id="u1", channel="web", text="смени email на new@corp.com")
    cls = await _classifier().classify(env, profile)
    email_actions = [a for a in cls.actions if a.action == "change_email"]
    assert email_actions and email_actions[0].entities == {"new_email": "new@corp.com"}


async def test_fallback_extracts_enum_synonym(profile):
    env = Envelope(user_id="u1", channel="web", text="поменяй язык на английский")
    cls = await _classifier().classify(env, profile)
    lang = [a for a in cls.actions if a.action == "set_language"]
    assert lang and lang[0].entities == {"lang": "en"}


async def test_fallback_multi_action(profile):
    env = Envelope(
        user_id="u1", channel="web", text="смени язык на русский и email на a@b.com"
    )
    cls = await _classifier().classify(env, profile)
    names = {a.action for a in cls.actions if a.type == ActionType.profile_action}
    assert {"set_language", "change_email"} <= names


async def test_unmatched_text_becomes_rag_query(profile):
    env = Envelope(user_id="u1", channel="web", text="как оформить отпуск")
    cls = await _classifier().classify(env, profile)
    assert cls.actions[0].type == ActionType.rag_query
    assert cls.actions[0].query == "как оформить отпуск"


async def test_backfill_fills_gaps_but_llm_wins(profile):
    """LLM found the action but left entities empty -> backfill from text;
    an LLM-provided value must NOT be overwritten."""
    clf = _classifier()
    env = Envelope(user_id="u1", channel="web", text="смени email на fromtext@corp.com")

    # LLM identified the action but extracted nothing -> gap gets filled.
    empty = Classification(
        actions=[ClassifiedAction(type=ActionType.profile_action, action="change_email")]
    )
    filled = clf._backfill_entities(empty, env)
    assert filled.actions[0].entities == {"new_email": "fromtext@corp.com"}

    # LLM already provided a value -> backfill leaves it alone.
    given = Classification(
        actions=[
            ClassifiedAction(
                type=ActionType.profile_action,
                action="change_email",
                entities={"new_email": "llm@corp.com"},
            )
        ]
    )
    kept = clf._backfill_entities(given, env)
    assert kept.actions[0].entities == {"new_email": "llm@corp.com"}
