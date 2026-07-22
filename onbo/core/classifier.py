"""Classifier: turns one user message into a LIST of actions (multi-action)."""
from __future__ import annotations

import re

from .llm import LLM
from .schemas import ActionType, Classification, ClassifiedAction, Envelope, Profile

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Generic verbs to drop when deriving keywords from an action's description,
# so "Сменить пароль" -> keyword "пароль" (config-driven, not hard-coded).
_STOP_WORDS = {"сменить", "изменить", "поменять", "change", "set", "update", "интерфейса"}
# Enum-value synonyms for slot-filling in the no-LLM fallback.
_ENUM_SYNONYMS = {"en": ("англ", "english", "en"), "ru": ("рус", "russian", "ru")}


class Classifier:
    def __init__(
        self,
        llm: LLM,
        actions: dict,
        *,
        actions_enabled: bool = True,
        rag_enabled: bool = True,
    ) -> None:
        self._llm = llm
        self._actions = actions  # name -> ActionSpec
        # Feature flags (config.features): when actions are off the classifier
        # offers none (everything falls through to RAG); when RAG is off it
        # answers no free-text questions (actions-only assistant).
        self._actions_enabled = actions_enabled
        self._rag_enabled = rag_enabled

    def _catalog(self) -> str:
        if not self._actions_enabled:
            return "(profile actions disabled)"
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
            classification = await self._llm.structured(
                [{"role": "user", "content": prompt}], Classification
            )
        except Exception:  # noqa: BLE001
            # Any LLM failure — not installed (LLMUnavailable), endpoint unreachable,
            # or invalid JSON from a small local model — degrades to the heuristic
            # fallback so the pipeline always returns a result.
            return self._apply_features(self._fallback(env))
        return self._apply_features(self._backfill_entities(classification, env))

    def _apply_features(self, classification: Classification) -> Classification:
        """Drop action types the feature flags disable (safety net over the catalog)."""
        classification.actions = [
            a
            for a in classification.actions
            if not (a.type == ActionType.profile_action and not self._actions_enabled)
            and not (a.type == ActionType.rag_query and not self._rag_enabled)
        ]
        return classification

    def _backfill_entities(self, classification: Classification, env: Envelope) -> Classification:
        """Fill entities the LLM identified an action for but failed to extract.

        Small local models often split a message into the right actions yet leave
        `entities` empty. We top up missing params from the raw text with the same
        regex slot-filler as the no-LLM fallback (LLM-provided values win — we only
        fill gaps). Entities are action params (email, language), never the access
        filter, so this cannot widen anyone's visibility.
        """
        for action in classification.actions:
            if action.type != ActionType.profile_action:
                continue
            spec = self._actions.get(action.action or "")
            if spec is None:
                continue
            for name, value in self._extract_entities(spec, env.text).items():
                action.entities.setdefault(name, value)
        return classification

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
        if self._actions_enabled:
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
