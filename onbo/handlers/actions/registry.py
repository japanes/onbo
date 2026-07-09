"""Action registry: load actions.yaml into typed specs and resolve handlers.

Adding a new action = a new file under ``handlers/actions/`` exposing a
``handler`` instance + one entry in ``config/actions.yaml``. Core is untouched.
"""
from __future__ import annotations

import importlib

import yaml
from pydantic import BaseModel, Field, model_validator

from ...config import config_dir
from ...core.schemas import ActionMode


class ParamSpec(BaseModel):
    type: str = "string"           # string | email | enum | ...
    required: bool = False
    values: list[str] | None = None  # allowed values for enum


class ActionSpec(BaseModel):
    name: str
    description: str = ""
    mode: ActionMode = ActionMode.chat
    sensitive: bool = False
    link_url: str | None = None
    confirm_prompt: str | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    handler: str | None = None      # dotted module path exposing `handler`

    @model_validator(mode="after")
    def _sensitive_is_always_link(self) -> "ActionSpec":
        # Safety rule: sensitive data can only ever be handed out as a link.
        if self.sensitive:
            self.mode = ActionMode.link
        return self


def load_action_specs() -> dict[str, ActionSpec]:
    path = config_dir() / "actions.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: dict[str, ActionSpec] = {}
    for name, body in (data.get("actions") or {}).items():
        specs[name] = ActionSpec(name=name, **(body or {}))
    return specs


class ActionRegistry:
    """Lazily import and cache handler instances declared by ``spec.handler``."""

    def __init__(self, specs: dict[str, ActionSpec]) -> None:
        self._specs = specs
        self._cache: dict[str, object] = {}

    def get(self, name: str):
        if name in self._cache:
            return self._cache[name]
        spec = self._specs.get(name)
        handler = self._resolve(spec.handler) if spec and spec.handler else None
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
