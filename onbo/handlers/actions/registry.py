"""Action registry: load actions.yaml into typed specs and resolve handlers.

Adding a new action = a new file under ``handlers/actions/`` exposing a
``handler`` instance + one entry in ``config/actions.yaml``. Core is untouched.
"""
from __future__ import annotations

import importlib

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ...config import config_dir
from ...core.schemas import ActionMode


class ParamSpec(BaseModel):
    type: str = "string"           # string | email | enum | ...
    required: bool = False
    values: list[str] | None = None  # allowed values for enum


class ApiSpec(BaseModel):
    """How to call the target product's backend for this action.

    ``url``/``path`` and string values in ``body``/``query`` are templated with
    ``{user_id}`` (from the profile) and ``{param}`` (from the action entities).

    Two ways to say where the request goes:

    - ``url`` — a full, absolute address (``https://app.example.com/api/projects``).
      Self-contained: this file alone says where every action lands, which is what
      an installation with several backends — or no single "product base" at all —
      needs. Preferred.
    - ``path`` — relative, joined onto ``product.base_url`` from settings.yaml.
      Kept for installations that do have one backend and would rather name it once.

    ``url`` wins when both are present.
    """
    method: str = "POST"
    url: str = ""
    path: str = ""
    body: dict = Field(default_factory=dict)
    query: dict = Field(default_factory=dict)
    success_message: str | None = None   # templated; shown when the call succeeds


class ActionSpec(BaseModel):
    name: str
    description: str = ""
    mode: ActionMode = ActionMode.chat
    sensitive: bool = False
    link_url: str | None = None
    confirm_prompt: str | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    handler: str | None = None      # dotted module path exposing `handler`
    api: ApiSpec | None = None      # declarative HTTP call (no Python needed)
    # Audience filter (used by welcome/about), same semantics as the KB:
    # empty = available to everyone.
    department: str | None = None
    roles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sensitive_is_always_link(self) -> "ActionSpec":
        # Safety rule: sensitive data can only ever be handed out as a link.
        if self.sensitive:
            self.mode = ActionMode.link
        return self


def spec_visible_to(spec, profile) -> bool:
    """Is this action/pipeline available to ``profile``?

    Same rule as the KB access filter (rag/qdrant_store): visible if it sets no
    department (public) or the department matches, AND — when it restricts roles —
    the user holds one of them. Empty restrictions = visible to everyone.
    """
    department = getattr(spec, "department", None)
    roles = getattr(spec, "roles", None) or []
    dept_ok = department is None or department == profile.department
    roles_ok = not roles or bool(set(roles) & set(profile.roles or []))
    return dept_ok and roles_ok


class PipelineStep(BaseModel):
    """One step of a pipeline: an existing action + its (templated) params."""
    action: str
    params: dict[str, str] = Field(default_factory=dict)  # values templated with {param}


class PipelineSpec(BaseModel):
    """A named sequence of actions run on a single command (one confirmation).

    Structurally compatible with :class:`ActionSpec` for the fields the router and
    classifier read (``name``/``description``/``mode``/``params``/``confirm_prompt``/
    ``sensitive``/``link_url``), so pipelines flow through the same routing path and
    show up in the action catalog and ``about`` self-doc without special-casing.
    """
    name: str
    description: str = ""
    mode: ActionMode = ActionMode.confirm      # chat | confirm (link is forbidden below)
    confirm_prompt: str | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    steps: list[PipelineStep] = Field(default_factory=list)
    on_error: str = "stop"                     # stop | continue
    # Audience filter (welcome/about), same semantics as the KB: empty = everyone.
    department: str | None = None
    roles: list[str] = Field(default_factory=list)
    # Fixed for duck-typing with ActionSpec — a pipeline is never sensitive/link.
    sensitive: bool = False
    link_url: str | None = None

    @field_validator("mode")
    @classmethod
    def _no_link_mode(cls, value: ActionMode) -> ActionMode:
        # A pipeline mutates several things at once; a bare link makes no sense.
        if value == ActionMode.link:
            raise ValueError("pipeline mode cannot be 'link'")
        return value


def load_action_specs() -> dict[str, ActionSpec]:
    path = config_dir() / "actions.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: dict[str, ActionSpec] = {}
    for name, body in (data.get("actions") or {}).items():
        specs[name] = ActionSpec(name=name, **(body or {}))
    return specs


def validate_pipeline(spec: PipelineSpec, actions: dict[str, ActionSpec]) -> None:
    """Reject a pipeline whose steps reference a missing or sensitive/link action.

    Raises ``ValueError`` with a user-facing message. A pipeline may only chain
    plain ``chat``/``confirm`` actions — never a sensitive one (those are handed
    out as links and must not run unattended inside a batch).
    """
    if not spec.steps:
        raise ValueError(f"пайплайн «{spec.name}» не содержит шагов")
    for step in spec.steps:
        target = actions.get(step.action)
        if target is None:
            raise ValueError(
                f"пайплайн «{spec.name}»: шаг ссылается на несуществующее действие «{step.action}»"
            )
        if target.sensitive or target.mode == ActionMode.link:
            raise ValueError(
                f"пайплайн «{spec.name}»: шаг «{step.action}» — чувствительное действие "
                f"(mode: link), в пайплайне запрещено"
            )


def load_pipeline_specs(actions: dict[str, ActionSpec]) -> dict[str, PipelineSpec]:
    """Load the ``pipelines:`` block, validating each against ``actions``.

    Names must not collide with plain actions (they share one routing namespace).
    """
    path = config_dir() / "actions.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: dict[str, PipelineSpec] = {}
    for name, body in (data.get("pipelines") or {}).items():
        if name in actions:
            raise ValueError(f"имя пайплайна «{name}» конфликтует с действием")
        spec = PipelineSpec(name=name, **(body or {}))
        validate_pipeline(spec, actions)
        specs[name] = spec
    return specs


class ActionRegistry:
    """Lazily import and cache handler instances declared by ``spec.handler``.

    ``specs`` is the merged map of plain actions *and* pipelines (they share one
    name namespace so pipelines route like any other action); ``pipelines`` marks
    which names resolve to a :class:`PipelineHandler` instead of a leaf handler.
    """

    def __init__(
        self,
        specs: dict[str, ActionSpec],
        pipelines: dict[str, PipelineSpec] | None = None,
    ) -> None:
        self._specs = specs
        self._pipelines = pipelines or {}
        self._cache: dict[str, object] = {}

    def get(self, name: str):
        if name in self._cache:
            return self._cache[name]
        if name in self._pipelines:
            from .pipeline import PipelineHandler

            handler = PipelineHandler(self._pipelines[name], self._specs, self)
            self._cache[name] = handler
            return handler
        spec = self._specs.get(name)
        handler = self._resolve(spec.handler) if spec and spec.handler else None
        # No hand-written handler but a declarative `api:` block -> zero-Python action.
        if handler is None and spec is not None and spec.api is not None:
            from .http_action import GenericHTTPHandler

            handler = GenericHTTPHandler()
        # Give the handler its spec so execute() can find the api block / messages.
        if handler is not None and spec is not None:
            handler.spec = spec
        self._cache[name] = handler
        return handler

    @staticmethod
    def _resolve(dotted: str):
        # Accept both "onbo.handlers.actions.x" and "handlers.actions.x".
        for candidate in (dotted, f"onbo.{dotted}"):
            try:
                module = importlib.import_module(candidate)
            except ImportError:
                continue
            return getattr(module, "handler", None)
        return None
