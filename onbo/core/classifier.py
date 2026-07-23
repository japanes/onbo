"""Classifier: turns one user message into a LIST of actions (multi-action)."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .llm import LLM
from .schemas import (
    ActionType,
    Classification,
    ClassifiedAction,
    Envelope,
    Profile,
    drop_blank_entities,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Generic verbs to drop when deriving keywords from an action's description,
# so "Сменить пароль" -> keyword "пароль" (config-driven, not hard-coded).
_STOP_WORDS = {"сменить", "изменить", "поменять", "change", "set", "update", "интерфейса"}
# Enum-value synonyms for slot-filling in the no-LLM fallback.
_ENUM_SYNONYMS = {"en": ("англ", "english", "en"), "ru": ("рус", "russian", "ru")}
# A bare answer to a single question: "12", "#12", "PRJ-7", "a@b.com". Anything
# with a digit in it and nothing else around it — see `fill` for why.
_BARE_ID_RE = re.compile(r"^[#№]?[\w.@:/-]{1,64}$")


class _FilledParams(BaseModel):
    """What the model found in a reply to «чего не хватает»."""

    values: dict[str, str] = Field(default_factory=dict)


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

    @staticmethod
    def _param_line(name, param) -> str:
        """One parameter, described well enough to be extracted or asked for."""
        notes = []
        if param.required:
            notes.append("required")
        if param.values:
            notes.append("one of: " + ", ".join(param.values))
        elif param.type not in ("string", ""):
            notes.append(param.type)
        suffix = f" [{'; '.join(notes)}]" if notes else ""
        meaning = f" — {param.description}" if param.description else ""
        return f"    {name}{suffix}{meaning}"

    def _catalog(self) -> str:
        """The action list as the model sees it.

        A bare `(params: project_id, platform)` tells the model nothing about
        what those are, so it fills them with plausible nonsense or with null.
        Each parameter is therefore listed with what it means, whether it is
        required and which values are allowed.
        """
        if not self._actions_enabled:
            return "(profile actions disabled)"
        lines = []
        for spec in self._actions.values():
            lines.append(f"- {spec.name}: {spec.description}")
            for name, param in spec.params.items():
                lines.append(self._param_line(name, param))
            if not spec.params:
                lines.append("    (no parameters)")
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
            "Emit every distinct request as its own action. Set confidence in [0,1].\n"
            "Extract only values the message actually states. Never guess a value, "
            "and never emit null or an empty string — leave the parameter out "
            "instead. A missing parameter is asked for; an invented one is acted on."
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

    async def fill(self, spec, names: list[str], text: str) -> dict:
        """Read ``names`` out of a message answering the question about them.

        Used when an action was parked for want of a required parameter and the
        person just replied. Cheap paths first — an enum value, an email, a bare
        one-word answer to a single question — and the model only for what is
        left, so «в проекте 12» does not cost a round trip when «12» does not.

        Returns only what the message really contains: an empty dict means the
        person did not answer, and the caller drops the parked action instead of
        asking again forever.
        """
        found = {
            name: value
            for name, value in self._extract_entities(spec, text).items()
            if name in names
        }
        stripped = text.strip()
        if (
            len(names) == 1
            and names[0] not in found
            and _BARE_ID_RE.match(stripped)
            and any(ch.isdigit() for ch in stripped)
        ):
            # Only for something that looks like an identifier, not for any single
            # word: «спасибо» is a reply to one open question too, and the model
            # can tell that apart from an answer where a regex cannot.
            found[names[0]] = stripped.lstrip("#№")

        missing = [name for name in names if name not in found]
        if not missing:
            return found

        lines = "\n".join(self._param_line(name, spec.params[name]) for name in missing)
        prompt = (
            f"The user was asked for the missing details of «{spec.description}» "
            f"and replied: {text!r}\n\n"
            f"Wanted:\n{lines}\n\n"
            'Return {"values": {...}} with only the parameters this reply actually '
            "states. Omit anything it does not say — do not guess, do not use null."
        )
        try:
            filled = await self._llm.structured([{"role": "user", "content": prompt}], _FilledParams)
        except Exception:  # noqa: BLE001 - no LLM configured, or unusable output
            return found
        for name, value in drop_blank_entities(filled.values).items():
            if name not in missing:
                continue
            param = spec.params.get(name)
            if param and param.values and str(value) not in param.values:
                continue  # a value outside the allowed set is not an answer
            found[name] = value
        return found

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
