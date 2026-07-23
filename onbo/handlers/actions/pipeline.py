"""Pipeline handler: run a sequence of actions from a single command.

A pipeline (see ``PipelineSpec`` in registry.py) chains existing actions —
"оформить заказ" → накладная себе → накладная клиенту → отправка. The whole
chain asks for one confirmation (``mode: confirm``); on Ok this handler runs each
step in order, substituting ``{param}`` values from the pipeline's own entities,
and reports a single aggregated result listing what ran.

``on_error: stop`` (default) halts on the first failed step and honestly says
which steps did and did not run; ``continue`` runs the rest regardless.
"""
from __future__ import annotations

from ...core.schemas import ActionResult, Profile, ResultStatus
from .base import ActionHandler
from .http_action import render_map

_OK_STATUSES = {ResultStatus.done, ResultStatus.dry_run}


class PipelineHandler(ActionHandler):
    def __init__(self, spec, actions: dict, registry) -> None:
        self.spec = spec
        self._actions = actions      # name -> ActionSpec (for step descriptions)
        self._registry = registry    # resolves each step's leaf handler

    async def validate(self, entities: dict) -> dict:
        # The router already checked the pipeline's own required params; nothing
        # else to slot-fill here — per-step params are templated at execute time.
        return entities

    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        ctx = {"user_id": profile.user_id, **entities}
        lines: list[str] = []
        statuses: list[ResultStatus] = []
        stopped_at: int | None = None

        for index, step in enumerate(self.spec.steps):
            target = self._actions.get(step.action)
            step_label = (target.description if target else None) or step.action
            handler = self._registry.get(step.action)
            if handler is None:
                res = ActionResult(
                    status=ResultStatus.failed,
                    action=step.action,
                    message=f"обработчик действия «{step.action}» не найден",
                )
            else:
                step_entities = render_map(step.params, ctx)
                try:
                    step_entities = await handler.validate(step_entities)
                    res = await handler.execute(profile, step_entities)
                except ValueError as exc:  # validation rejected the step's input
                    res = ActionResult(
                        status=ResultStatus.failed, action=step.action, message=str(exc)
                    )

            statuses.append(res.status)
            mark = "✓" if res.status in _OK_STATUSES else "✗"
            lines.append(f"{mark} {step_label}: {res.message}")

            if res.status == ResultStatus.failed and self.spec.on_error == "stop":
                stopped_at = index
                break

        if stopped_at is not None:
            skipped = [
                (self._actions.get(s.action).description if self._actions.get(s.action) else s.action)
                for s in self.spec.steps[stopped_at + 1:]
            ]
            if skipped:
                lines.append("Не выполнено (остановка после ошибки): " + ", ".join(skipped) + ".")

        # One aggregated status: fail if any step failed; demo-only if all dry-run.
        if any(s == ResultStatus.failed for s in statuses):
            overall = ResultStatus.failed
        elif statuses and all(s == ResultStatus.dry_run for s in statuses):
            overall = ResultStatus.dry_run
        else:
            overall = ResultStatus.done

        header = f"Пайплайн «{self.spec.description or self.spec.name}»:"
        return ActionResult(
            status=overall,
            action=self.spec.name,
            message="\n".join([header, *lines]),
        )
