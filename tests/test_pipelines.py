"""Action pipelines: spec validation + sequential execution.

The pipeline chains existing actions on one command (one confirmation). We test
the validator (a step may not reference a missing or sensitive/link action) and
the handler (param substitution, stop-vs-continue on error) with fake leaf
handlers — no HTTP, no classifier.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from onbo.core.schemas import ActionMode, ActionResult, Profile, ResultStatus
from onbo.handlers.actions.pipeline import PipelineHandler
from onbo.handlers.actions.registry import (
    ActionSpec,
    PipelineSpec,
    PipelineStep,
    load_action_specs,
    load_pipeline_specs,
    validate_pipeline,
)


# -- spec validation ---------------------------------------------------------


def test_validate_rejects_link_step():
    actions = {"reset_pw": ActionSpec(name="reset_pw", mode=ActionMode.link, sensitive=True)}
    spec = PipelineSpec(name="p", steps=[PipelineStep(action="reset_pw")])
    with pytest.raises(ValueError, match="чувствительное"):
        validate_pipeline(spec, actions)


def test_validate_rejects_missing_action():
    spec = PipelineSpec(name="p", steps=[PipelineStep(action="nope")])
    with pytest.raises(ValueError, match="несуществующее"):
        validate_pipeline(spec, {})


def test_validate_rejects_empty_pipeline():
    with pytest.raises(ValueError, match="не содержит шагов"):
        validate_pipeline(PipelineSpec(name="p"), {})


def test_pipeline_mode_link_is_rejected():
    with pytest.raises(ValidationError):
        PipelineSpec(name="p", mode=ActionMode.link)


def test_real_config_new_order_loads_and_validates():
    # config/actions.yaml ships the new_order pipeline; it must validate.
    actions = load_action_specs()
    pipelines = load_pipeline_specs(actions)
    assert "new_order" in pipelines
    assert [s.action for s in pipelines["new_order"].steps] == [
        "create_invoice_internal", "create_invoice_client", "send_invoice_to_client",
    ]


# -- execution ---------------------------------------------------------------


class _RecordingHandler:
    def __init__(self, result: ActionResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def validate(self, entities: dict) -> dict:
        return entities

    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        self.calls.append(entities)
        return self._result


class _FakeRegistry:
    def __init__(self, handlers: dict) -> None:
        self._handlers = handlers

    def get(self, name: str):
        return self._handlers.get(name)


def _two_step_actions() -> dict:
    return {
        "a": ActionSpec(name="a", description="Шаг A", mode=ActionMode.chat),
        "b": ActionSpec(name="b", description="Шаг B", mode=ActionMode.chat),
    }


async def test_pipeline_runs_all_steps_and_substitutes_params():
    spec = PipelineSpec(name="p", description="Пайп", steps=[
        PipelineStep(action="a", params={"order_id": "{order_id}"}),
        PipelineStep(action="b", params={"order_id": "{order_id}"}),
    ])
    ha = _RecordingHandler(ActionResult(status=ResultStatus.done, action="a", message="ok A"))
    hb = _RecordingHandler(ActionResult(status=ResultStatus.done, action="b", message="ok B"))
    handler = PipelineHandler(spec, _two_step_actions(), _FakeRegistry({"a": ha, "b": hb}))

    res = await handler.execute(Profile(user_id="u1"), {"order_id": "42"})

    assert res.status == ResultStatus.done
    assert ha.calls == [{"order_id": "42"}]      # {order_id} substituted
    assert hb.calls == [{"order_id": "42"}]
    assert "ok A" in res.message and "ok B" in res.message


async def test_pipeline_stops_on_error():
    spec = PipelineSpec(name="p", description="Пайп", on_error="stop", steps=[
        PipelineStep(action="a"), PipelineStep(action="b"),
    ])
    ha = _RecordingHandler(ActionResult(status=ResultStatus.failed, action="a", message="боль"))
    hb = _RecordingHandler(ActionResult(status=ResultStatus.done, action="b", message="ok B"))
    handler = PipelineHandler(spec, _two_step_actions(), _FakeRegistry({"a": ha, "b": hb}))

    res = await handler.execute(Profile(user_id="u1"), {})

    assert res.status == ResultStatus.failed
    assert hb.calls == []                        # step b never ran
    assert "Не выполнено" in res.message         # honest about the skipped step


async def test_pipeline_continue_runs_remaining_steps():
    spec = PipelineSpec(name="p", description="Пайп", on_error="continue", steps=[
        PipelineStep(action="a"), PipelineStep(action="b"),
    ])
    ha = _RecordingHandler(ActionResult(status=ResultStatus.failed, action="a", message="боль"))
    hb = _RecordingHandler(ActionResult(status=ResultStatus.done, action="b", message="ok B"))
    handler = PipelineHandler(spec, _two_step_actions(), _FakeRegistry({"a": ha, "b": hb}))

    res = await handler.execute(Profile(user_id="u1"), {})

    assert res.status == ResultStatus.failed     # a failure still surfaces
    assert hb.calls == [{}]                       # but b ran anyway
