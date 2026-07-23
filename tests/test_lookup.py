"""«Милано» → id 3: parameters whose values live in the product's directory.

The list of warehouses (projects, tariffs, price lists) is different in every
installation and changes without anyone editing actions.yaml, so it cannot be
written down as `values:`. The parameter says where to read it instead, and the
engine decides: one match — substitute it, several — ask which, none — show what
does exist.
"""
from __future__ import annotations

import httpx
import pytest

from onbo.config import ProductSettings, Settings
from onbo.core.classifier import Classifier
from onbo.core.pipeline import Pipeline
from onbo.core.router import Router
from onbo.core.schemas import (
    ActionMode,
    ActionType,
    ClassifiedAction,
    Envelope,
    Profile,
    ResultStatus,
)
from onbo.handlers.actions import lookup
from onbo.handlers.actions.lookup import resolve_lookups
from onbo.handlers.actions.registry import ActionSpec, LookupSpec, ParamSpec
from tests.conftest import FakeRegistry, FakeSession, RecordingHandler

PROFILE = Profile(user_id="u1")

WAREHOUSES = [
    {"id": 3, "name": "Milano", "code": "milano"},
    {"id": 4, "name": "Milano Nord", "code": "milano_nord"},
    {"id": 7, "name": "Torino", "code": "torino"},
]


def _spec(**overrides) -> ActionSpec:
    fields = {
        "url": "https://app.example.com/api/orders/{order_id}/warehouses",
        "items": "data",
        "value": "id",
        "label": "name",
        "match": ["code"],
    }
    fields.update(overrides)
    return ActionSpec(
        name="ship_order",
        description="Отгрузить заказ",
        mode=ActionMode.confirm,
        confirm_prompt="Отгрузить заказ #{order_id} со склада {warehouse_label}?",
        params={
            "order_id": ParamSpec(required=True, description="номер заказа"),
            "warehouse": ParamSpec(
                required=True, description="с какого склада", lookup=LookupSpec(**fields)
            ),
        },
    )


class _FakeResp:
    def __init__(self, payload, status_code: int) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeClient:
    """Serves a canned directory and records every request made for it."""

    calls: list = []
    payload = {"data": WAREHOUSES}
    status = 200

    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None, params=None, headers=None):
        _FakeClient.calls.append({"method": method, "url": url, "params": params, "headers": headers})
        return _FakeResp(_FakeClient.payload, _FakeClient.status)


@pytest.fixture(autouse=True)
def _backend(monkeypatch):
    settings = Settings()
    settings.product = ProductSettings(base_url="https://app.example.com", api_key="service-key")
    monkeypatch.setattr(lookup, "load_settings", lambda: settings)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = []
    _FakeClient.payload = {"data": WAREHOUSES}
    _FakeClient.status = 200
    lookup.clear_cache()
    yield
    lookup.clear_cache()


# -- what a person said, and what the API gets --------------------------------

async def test_one_match_becomes_the_id_the_api_wants():
    """Said in Russian, matched against a Latin row name — the usual case."""
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "Торино"}, PROFILE)
    assert res.question is None and res.error is None
    assert res.entities["warehouse"] == "7"
    # ...and the readable name survives, for the confirmation the person reads.
    assert res.entities["warehouse_label"] == "Torino"


async def test_an_exact_hit_is_not_made_ambiguous_by_a_longer_neighbour():
    """«Milano» must not become a question just because «Milano Nord» exists."""
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "Милано"}, PROFILE)
    assert res.question is None
    assert res.entities["warehouse"] == "3"


async def test_several_matches_are_asked_about_by_name():
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "милан"}, PROFILE)
    assert "Milano" in res.question and "Milano Nord" in res.question
    assert res.asked == "warehouse"
    # The unusable word is thrown away, or the reply would never be read as an
    # answer to this question — and could reach the product as a real value.
    assert "warehouse" not in res.entities


async def test_an_unknown_word_gets_the_real_list_back():
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "Неаполь"}, PROFILE)
    assert "«Неаполь» — такого значения нет" in res.question
    assert "Milano, Milano Nord, Torino" in res.question


async def test_nothing_said_asks_with_the_live_list_not_a_bare_name():
    res = await resolve_lookups(_spec(), {"order_id": "12"}, PROFILE)
    assert res.question == "Уточните: с какого склада — Milano, Milano Nord, Torino."
    assert res.asked == "warehouse"


async def test_an_id_that_is_already_correct_is_left_alone():
    """A repeated turn, or a page that passed the id in, needs no re-guessing."""
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "7"}, PROFILE)
    assert res.question is None
    assert res.entities["warehouse"] == "7"


async def test_an_empty_directory_is_said_out_loud():
    _FakeClient.payload = {"data": []}
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "Торино"}, PROFILE)
    assert "Не нашёл ни одного значения" in res.question


async def test_a_parameter_without_a_lookup_is_untouched():
    spec = ActionSpec(
        name="x", description="x", params={"note": ParamSpec(description="заметка")}
    )
    res = await resolve_lookups(spec, {"note": "Милано"}, PROFILE)
    assert res.entities == {"note": "Милано"} and _FakeClient.calls == []


async def test_an_optional_directory_nobody_mentioned_costs_nothing():
    spec = _spec()
    spec.params["warehouse"].required = False
    res = await resolve_lookups(spec, {"order_id": "12"}, PROFILE)
    assert res.question is None and _FakeClient.calls == []


# -- how the directory is read ------------------------------------------------

async def test_the_address_is_templated_and_read_as_this_person():
    await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "torino"}, PROFILE)
    call = _FakeClient.calls[0]
    assert call["url"] == "https://app.example.com/api/orders/12/warehouses"
    assert call["method"] == "GET"
    assert call["headers"]["Authorization"] == "Bearer service-key"


async def test_the_callers_own_credential_wins_over_the_service_key():
    """The list can only ever hold what this person is allowed to see."""
    who = Profile(user_id="u1", product_token="his-own-token")
    await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "torino"}, who)
    assert _FakeClient.calls[0]["headers"]["Authorization"] == "Bearer his-own-token"


async def test_a_relative_path_hangs_off_the_configured_product():
    res = await resolve_lookups(
        _spec(url="", path="/api/warehouses"), {"order_id": "12", "warehouse": "torino"}, PROFILE
    )
    assert _FakeClient.calls[0]["url"] == "https://app.example.com/api/warehouses"
    assert res.entities["warehouse"] == "7"


async def test_a_directory_scoped_by_a_parameter_we_do_not_have_yet_is_not_read():
    """No order id -> no request: the missing-parameter check asks for it first."""
    res = await resolve_lookups(_spec(), {"warehouse": "torino"}, PROFILE)
    assert _FakeClient.calls == []
    assert res.question is None and res.error is None


async def test_a_query_string_is_templated_too_and_waits_for_its_value():
    spec = _spec(url="https://app.example.com/api/warehouses", query={"order": "{order_id}"})
    assert (await resolve_lookups(spec, {"warehouse": "torino"}, PROFILE)).question is None
    assert _FakeClient.calls == []

    await resolve_lookups(spec, {"order_id": "12", "warehouse": "torino"}, PROFILE)
    assert _FakeClient.calls[0]["params"] == {"order": "12"}


async def test_the_same_list_is_not_fetched_twice_in_a_row():
    for _ in range(3):
        await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "torino"}, PROFILE)
    assert len(_FakeClient.calls) == 1


async def test_two_people_never_share_a_cached_list():
    said = {"order_id": "12", "warehouse": "torino"}
    await resolve_lookups(_spec(), said, Profile(user_id="a", product_token="token-a"))
    await resolve_lookups(_spec(), said, Profile(user_id="b", product_token="token-b"))
    assert len(_FakeClient.calls) == 2


async def test_an_unreachable_directory_is_an_honest_error_not_a_guess():
    _FakeClient.status = 500
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "torino"}, PROFILE)
    assert res.question is None
    assert "справочник" in res.error and "500" in res.error


async def test_a_response_that_is_not_a_list_is_reported_too():
    _FakeClient.payload = {"data": {"oops": 1}}
    res = await resolve_lookups(_spec(), {"order_id": "12", "warehouse": "torino"}, PROFILE)
    assert "не содержит списка" in res.error


async def test_a_flat_response_needs_no_items_path():
    _FakeClient.payload = WAREHOUSES
    res = await resolve_lookups(
        _spec(items=""), {"order_id": "12", "warehouse": "torino"}, PROFILE
    )
    assert res.entities["warehouse"] == "7"


# -- and how all of it behaves in an actual conversation ----------------------

class _NoLLM:
    async def structured(self, messages, schema):
        raise RuntimeError("no LLM")


def _pipeline(handler):
    specs = {"ship_order": _spec()}
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.specs = specs
    pipeline.session = FakeSession()
    pipeline.classifier = Classifier(_NoLLM(), specs)
    pipeline.router = Router(specs, FakeRegistry(handler), None, None, pipeline.session)
    return pipeline


async def test_the_answer_to_which_warehouse_completes_the_action(profile):
    """The reply is read as an answer even though the parameter *looked* filled."""
    handler = RecordingHandler()
    pipeline = _pipeline(handler)

    asked = await pipeline.router.route(
        ClassifiedAction(
            type=ActionType.profile_action,
            action="ship_order",
            entities={"order_id": "12", "warehouse": "Неаполь"},
        ),
        profile,
    )
    assert asked.status == ResultStatus.needs_input
    assert "Milano" in asked.message
    # Without `wanted`, the parked action has every required value filled in and
    # the reply would fall through to the knowledge base.
    assert pipeline.session.awaiting[profile.user_id]["wanted"] == ["warehouse"]

    answered = await pipeline._resume_pending(
        Envelope(user_id=profile.user_id, channel="web", text="Торино"), profile
    )
    assert answered.status == ResultStatus.needs_confirm
    assert answered.confirm_prompt == "Отгрузить заказ #12 со склада Torino?"


async def test_the_confirmed_action_is_executed_with_the_id(profile):
    handler = RecordingHandler()
    pipeline = _pipeline(handler)
    await pipeline.router.route(
        ClassifiedAction(
            type=ActionType.profile_action,
            action="ship_order",
            entities={"order_id": "12", "warehouse": "nord"},
        ),
        profile,
    )
    entities = await pipeline.session.pop(profile.user_id, "ship_order")
    assert entities["warehouse"] == "4"
    assert entities["warehouse_label"] == "Milano Nord"
