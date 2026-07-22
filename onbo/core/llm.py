"""Thin async wrapper around LiteLLM so local and cloud models are interchangeable."""
from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel

from ..config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when litellm is not installed or no model is reachable."""


class LLM:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _request_params(self, overrides: dict) -> dict:
        """Merge configured knobs with per-call overrides, dropping the unset ones.

        Anything left as ``None`` never reaches the provider. That is the whole
        point: a flagship reasoning model errors out on ``temperature`` at all,
        so "not configured" has to mean "absent from the request", not "0.0".
        """
        cfg = self._s.llm
        params = {
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": cfg.max_tokens,
            "reasoning_effort": cfg.reasoning_effort,
            **cfg.params,
            **overrides,
        }
        return {name: value for name, value in params.items() if value is not None}

    async def complete(self, messages: list[dict], **overrides) -> str:
        """One completion. Keyword overrides beat settings.yaml for this call only."""
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise LLMUnavailable("litellm is not installed (pip install 'onbo[llm]')") from exc

        # Ask LiteLLM to strip params the target model rejects (reasoning_effort
        # on a plain chat model, say) rather than raising.
        litellm.drop_params = self._s.llm.drop_unsupported

        resp = await litellm.acompletion(
            model=self._s.llm.model,
            messages=messages,
            api_key=self._s.llm.api_key,
            api_base=self._s.llm.api_base,
            **self._request_params(overrides),
        )
        return resp["choices"][0]["message"]["content"] or ""

    async def structured(self, messages: list[dict], schema: Type[T]) -> T:
        """Ask the model for JSON matching ``schema`` and validate it with pydantic."""
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        system = {
            "role": "system",
            "content": (
                "You are a strict JSON generator. Return ONLY a JSON object that "
                "validates against this JSON Schema, with no prose or code fences:\n"
                + schema_json
            ),
        }
        # No temperature override here: whether determinism is even expressible
        # is a property of the configured model, so it belongs in settings.yaml.
        raw = await self.complete([system, *messages])
        return schema.model_validate_json(_extract_json(raw))


def _extract_json(text: str) -> str:
    """Best-effort extraction of a JSON object from a model reply."""
    text = text.strip()
    if text.startswith("```"):
        # Drop a leading ```json / ``` fence and anything after the closing fence.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text
