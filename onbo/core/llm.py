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

    async def complete(self, messages: list[dict], temperature: float = 0.2) -> str:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise LLMUnavailable("litellm is not installed (pip install 'onbo[llm]')") from exc

        resp = await litellm.acompletion(
            model=self._s.llm.model,
            messages=messages,
            temperature=temperature,
            api_key=self._s.llm.api_key,
            api_base=self._s.llm.api_base,
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
        raw = await self.complete([system, *messages], temperature=0.0)
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
