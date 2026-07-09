"""Classifier: turns one user message into a LIST of actions (multi-action)."""
from __future__ import annotations

import re

from .llm import LLM, LLMUnavailable
from .schemas import ActionType, Classification, ClassifiedAction, Envelope, Profile

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Generic verbs to drop when deriving keywords from an action's description,
# so "Сменить пароль" -> keyword "пароль" (config-driven, not hard-coded).
_STOP_WORDS = {"сменить", "изменить", "поменять", "change", "set", "update", "интерфейса"}
# Enum-value synonyms for slot-filling in the no-LLM fallback.
_ENUM_SYNONYMS = {"en": ("англ", "english", "en"), "ru": ("рус", "russian", "ru")}


class Classifier:
    def __init__(self, llm: LLM, actions: dict) -> None:
        self._llm = llm
        self._actions = actions  # name -> ActionSpec

    def _catalog(self) -> str:
        lines = []
        for spec in self._actions.values():
            params = ", ".join(spec.params.keys()) or "-"
            lines.append(f"- {spec.name}: {spec.description} (params: {params})")
        return "\n".join(lines) or "(no profile actions configured)"

    async def classify(self, env: Envelope, profile: Profile) -> Classification:
        prompt = (
            "Split the user's message into one or more actions.\n"
            "Action types:\n"
            "  profile_action — change a profile setting; set `action` from the catalog and extract `entities`.\n"
            "  rag_query — a question answerable from the knowledge base; put it in `query`.\n"
            "  about — asks what this assistant can do / how to use it.\n"
            "  unknown — cannot tell.\n\n"
            f"Profile actions catalog:\n{self._catalog()}\n\n"
            f"User message: {env.text!r}\n"
            "Emit every distinct request as its own action. Set confidence in [0,1]."
        )
        try:
            return await self._llm.structured([{"role": "user", "content": prompt}], Classification)
        except LLMUnavailable:
            return self._fallback(env)

    @staticmethod
    def _keywords(spec) -> list[str]:
        """Content words from the action's (localised) description."""
        words = re.findall(r"\w+", spec.description.lower())
        return [w for w in words if len(w) >= 4 and w not in _STOP_WORDS]

    def _extract_entities(self, spec, text: str) -> dict:
        """Best-effort slot-filling from raw text for the no-LLM fallback."""
        entities: dict = {}
        for name, param in spec.params.items():
            if param.type == "email":
                match = _EMAIL_RE.search(text)
                if match:
                    entities[name] = match.group(0)
            elif param.type == "enum" and param.values:
                for value in param.values:
                    if any(syn in text for syn in _ENUM_SYNONYMS.get(value, (value,))):
                        entities[name] = value
                        break
        return entities

    def _fallback(self, env: Envelope) -> Classification:
        """Heuristic so the skeleton stays runnable without an LLM configured.

        Keywords are derived from each action's description (config-driven), so it
        adapts to whatever actions.yaml defines. It is intentionally shallow — the
        real path uses the LLM; this only keeps demos and tests working offline.
        """
        text = env.text.lower()
        actions: list[ClassifiedAction] = []
        for spec in self._actions.values():
            if any(keyword in text for keyword in self._keywords(spec)):
                actions.append(
                    ClassifiedAction(
                        type=ActionType.profile_action,
                        action=spec.name,
                        entities=self._extract_entities(spec, text),
                        confidence=0.3,
                    )
                )
        if any(word in text for word in ("умеешь", "можешь", "что ты", "возможности")):
            actions.append(ClassifiedAction(type=ActionType.about, confidence=0.3))
        if not actions and text.strip():
            actions.append(ClassifiedAction(type=ActionType.rag_query, query=env.text, confidence=0.3))
        return Classification(actions=actions)
