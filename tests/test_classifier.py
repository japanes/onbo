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
        "create_post": ActionSpec(
            name="create_post",
            description="Создать пост",
            params={
                "project_id": ParamSpec(required=True, description="в каком проекте"),
                "platform": ParamSpec(
                    type="enum",
                    required=True,
                    values=["instagram", "telegram"],
                    description="площадка",
                ),
            },
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


# -- the catalog the model reads --------------------------------------------


def test_catalog_says_what_each_param_is():
    """A bare list of names is what makes a model answer `project_id: null`."""
    catalog = _classifier()._catalog()
    assert "project_id [required] — в каком проекте" in catalog
    assert "platform [required; one of: instagram, telegram] — площадка" in catalog


async def test_the_prompt_forbids_inventing_values(profile, monkeypatch):
    seen = {}

    class Recorder(BrokenLLM):
        async def structured(self, messages, schema):
            seen["prompt"] = messages[0]["content"]
            raise RuntimeError("no LLM")

    clf = Classifier(Recorder(), _actions())
    await clf.classify(Envelope(user_id="u1", channel="web", text="привет"), profile)
    assert "never emit null" in seen["prompt"]


# -- filling the gaps from the next message ----------------------------------


async def test_a_one_word_reply_answers_the_only_question():
    """«12» to «в каком проекте» costs no round trip to the model."""
    clf = _classifier()   # BrokenLLM: nothing but the cheap path can work here
    spec = _actions()["create_post"]
    assert await clf.fill(spec, ["project_id"], "12") == {"project_id": "12"}


async def test_an_enum_is_recognised_inside_a_sentence():
    clf = _classifier()
    spec = _actions()["create_post"]
    assert await clf.fill(spec, ["platform"], "давай в instagram") == {"platform": "instagram"}


async def test_an_unanswered_question_fills_nothing():
    """The caller reads an empty dict as «this message was about something else»."""
    clf = _classifier()
    spec = _actions()["create_post"]
    assert await clf.fill(spec, ["project_id", "platform"], "а какие вообще есть проекты?") == {}


async def test_the_model_fills_what_the_cheap_path_cannot():
    class Filler(BrokenLLM):
        async def structured(self, messages, schema):
            return schema(values={"project_id": "12", "platform": "telegram"})

    clf = Classifier(Filler(), _actions())
    filled = await clf.fill(_actions()["create_post"], ["project_id", "platform"], "в 12-м, в телегу")
    assert filled == {"project_id": "12", "platform": "telegram"}


async def test_a_value_outside_the_allowed_set_is_not_an_answer():
    class Inventor(BrokenLLM):
        async def structured(self, messages, schema):
            return schema(values={"platform": "myspace"})

    clf = Classifier(Inventor(), _actions())
    assert await clf.fill(_actions()["create_post"], ["platform"], "куда-нибудь") == {}


async def test_a_null_from_the_model_is_not_an_answer():
    class Nuller(BrokenLLM):
        async def structured(self, messages, schema):
            return schema(values={"project_id": "none"})

    clf = Classifier(Nuller(), _actions())
    assert await clf.fill(_actions()["create_post"], ["project_id"], "не знаю") == {}


async def test_a_bare_word_is_not_assumed_to_be_the_answer():
    """«спасибо» is also a one-word reply to a question — the model decides."""
    clf = _classifier()   # BrokenLLM: the cheap path is all there is
    assert await clf.fill(_actions()["create_post"], ["project_id"], "спасибо") == {}


async def test_the_hash_people_type_before_an_id_is_dropped():
    clf = _classifier()
    assert await clf.fill(_actions()["create_post"], ["project_id"], "#12") == {"project_id": "12"}
