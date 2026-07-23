"""Classifier: turns one user message into a LIST of actions (multi-action)."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..handlers.actions.registry import spec_visible_to
from .clock import now_line
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
# Asking for the capability list, in the no-LLM fallback. The greeting no longer
# prints that list, so this path has to actually catch the question. Phrases, not
# bare stems: «команда» alone is a team in half the products onbo plugs into.
_ABOUT_TRIGGERS = (
    "умеешь", "можешь", "что ты", "возможности",
    "список команд", "список действий", "какие команды", "help",
)


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
        retriever=None,
        shortlist_size: int = 12,
    ) -> None:
        self._llm = llm
        self._actions = actions  # name -> ActionSpec
        # Feature flags (config.features): when actions are off the classifier
        # offers none (everything falls through to RAG); when RAG is off it
        # answers no free-text questions (actions-only assistant).
        self._actions_enabled = actions_enabled
        self._rag_enabled = rag_enabled
        # Optional rag.Retriever: picks the commands worth showing for *this*
        # message (see _shortlist). Without one the whole catalogue is printed,
        # which is what onbo did before and still does whenever the index is not
        # usable — slower and dearer, never wrong.
        self._retriever = retriever
        self._shortlist_size = shortlist_size

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
        if getattr(param, "lookup", None):
            # The real value is a row id looked up afterwards (handlers/actions/
            # lookup.py). Asked for an id it cannot know, a model invents one —
            # so ask it for the person's own wording and resolve that.
            notes.append("copy the user's own wording; it is looked up afterwards")
        suffix = f" [{'; '.join(notes)}]" if notes else ""
        meaning = f" — {param.description}" if param.description else ""
        return f"    {name}{suffix}{meaning}"

    def _catalog(self, specs: dict) -> str:
        """The action list as the model sees it.

        A bare `(params: project_id, platform)` tells the model nothing about
        what those are, so it fills them with plausible nonsense or with null.
        Each parameter is therefore listed with what it means, whether it is
        required and which values are allowed.
        """
        if not self._actions_enabled:
            return "(profile actions disabled)"
        lines = []
        for spec in specs.values():
            lines.append(f"- {spec.name}: {spec.description}")
            for name, param in spec.params.items():
                lines.append(self._param_line(name, param))
            if not spec.params:
                lines.append("    (no parameters)")
        return "\n".join(lines) or "(no profile actions configured)"

    def _visible(self, profile: Profile) -> dict:
        """The commands this person is allowed to have at all."""
        return {
            name: spec
            for name, spec in self._actions.items()
            if spec_visible_to(spec, profile)
        }

    async def _shortlist(
        self, text: str, profile: Profile, parked: str | None = None
    ) -> dict:
        """The commands worth putting in front of the model for this message.

        The catalogue is searched, not printed: a vector query returns the dozen
        or so commands that sound like what was asked, and only those reach the
        prompt (see handlers/actions/index.py for why).

        Degrading is not optional. No retriever, a catalogue small enough that
        searching it saves nothing, Qdrant down, an empty index — every one of
        those falls back to the full list. A long prompt is expensive; a silent
        «no command matches» is broken.

        The shortlist is a union, never a replacement: whatever the vector found,
        plus the cheap keyword matches, plus the action currently parked in the
        session — the reply to «в каком проекте?» rarely resembles the command it
        belongs to.
        """
        if not self._actions_enabled:
            return {}   # nothing will be offered; do not pay for a search
        visible = self._visible(profile)
        if self._retriever is None or len(visible) <= self._shortlist_size:
            return visible
        try:
            found = await self._retriever.search_actions(
                text, profile, limit=self._shortlist_size
            )
        except Exception:  # noqa: BLE001 - Qdrant down, embedder missing, ...
            return visible
        names = {name for name in found if name in visible}
        if not names:
            return visible
        lowered = text.lower()
        names |= {
            name
            for name, spec in visible.items()
            if any(keyword in lowered for keyword in self._keywords(spec))
        }
        if parked and parked in visible:
            names.add(parked)
        return {name: spec for name, spec in visible.items() if name in names}

    async def classify(
        self, env: Envelope, profile: Profile, parked: str | None = None
    ) -> Classification:
        specs = await self._shortlist(env.text, profile, parked)
        prompt = (
            "Split the user's message into one or more actions.\n"
            "Action types:\n"
            "  profile_action — change a profile setting; set `action` from the catalog and extract `entities`.\n"
            "  rag_query — a question answerable from the knowledge base; put it in `query`.\n"
            "  about — asks what this assistant can do / how to use it, or asks for "
            "the list of available commands.\n"
            "  unknown — cannot tell.\n\n"
            # Only the plausible commands, not all of them — so the model has to
            # be told that "nothing here fits" is an allowed answer, or it picks
            # the least-bad line out of a list that never contained the right one.
            f"Profile actions catalog (a shortlist — if none of these is what the "
            f"message asks for, do not pick one):\n{self._catalog(specs)}\n\n"
            # Without this the model cannot turn «на 25 июля» into a date at all:
            # it has no clock, and the year is not in the sentence.
            f"{now_line(env.ts)}\n\n"
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
            return self._apply_features(self._fallback(env, specs))
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
        """Content words for this action: its description plus its ``keywords``.

        The explicit list is what actions.yaml wrote down as the wordings people
        really use, so it is taken as-is — short entries included. «пост» is four
        letters short of surviving the description filter and is exactly the word
        someone types.
        """
        words = re.findall(r"\w+", spec.description.lower())
        derived = [w for w in words if len(w) >= 4 and w not in _STOP_WORDS]
        explicit = [k.lower() for k in (getattr(spec, "keywords", None) or []) if k]
        return derived + explicit

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

    async def fill(self, spec, names: list[str], text: str, ts: str | None = None) -> dict:
        """Read ``names`` out of a message answering the question about them.

        Used when an action was parked for want of a required parameter and the
        person just replied. Cheap paths first — an enum value, an email, a bare
        one-word answer to a single question — and the model only for what is
        left, so «в проекте 12» does not cost a round trip when «12» does not.

        Returns only what the message really contains: an empty dict means the
        person did not answer, and the caller drops the parked action instead of
        asking again forever.

        ``ts`` is the caller's own clock (see core/clock.py): the answer to
        «на какую дату?» is «25 июля» just as often as it is a full timestamp.
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
            f"{now_line(ts)}\n\n"
            'Return {"values": {...}} with only the parameters this reply actually '
            "states. Omit anything it does not say — do not guess, do not use null."
        )
        try:
            filled = await self._llm.structured([{"role": "user", "content": prompt}], _FilledParams)
        except Exception:  # noqa: BLE001 - no LLM configured, or unusable output
            # Nothing to read the reply with. If the question was about a single
            # directory-backed value («с какого склада?»), the reply is that value
            # — and guessing costs nothing, because it is looked up in the real
            # directory right after: a reply that is not an answer comes back as
            # «такого значения нет» instead of reaching the product.
            only = spec.params.get(missing[0]) if len(missing) == 1 else None
            if getattr(only, "lookup", None) and 0 < len(stripped) <= 64 and "\n" not in stripped:
                found[missing[0]] = stripped
            return found
        for name, value in drop_blank_entities(filled.values).items():
            if name not in missing:
                continue
            param = spec.params.get(name)
            if param and param.values and str(value) not in param.values:
                continue  # a value outside the allowed set is not an answer
            found[name] = value
        return found

    def _fallback(self, env: Envelope, specs: dict | None = None) -> Classification:
        """Heuristic so the skeleton stays runnable without an LLM configured.

        Keywords are derived from each action's description (config-driven), so it
        adapts to whatever actions.yaml defines. It is intentionally shallow — the
        real path uses the LLM; this only keeps demos and tests working offline.

        It searches the same shortlist the model was given, not the whole
        catalogue: an action kept out of the prompt because it is not this
        person's must not come back in through the offline path.
        """
        text = env.text.lower()
        actions: list[ClassifiedAction] = []
        if self._actions_enabled:
            for spec in (self._actions if specs is None else specs).values():
                if any(keyword in text for keyword in self._keywords(spec)):
                    actions.append(
                        ClassifiedAction(
                            type=ActionType.profile_action,
                            action=spec.name,
                            entities=self._extract_entities(spec, text),
                            confidence=0.3,
                        )
                    )
        if any(word in text for word in _ABOUT_TRIGGERS):
            actions.append(ClassifiedAction(type=ActionType.about, confidence=0.3))
        if not actions and text.strip():
            actions.append(ClassifiedAction(type=ActionType.rag_query, query=env.text, confidence=0.3))
        return Classification(actions=actions)
