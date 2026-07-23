"""Shared data contracts (pydantic) used across the whole pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Attachment(BaseModel):
    kind: str  # "audio" | "image" | "file"
    content_type: str | None = None
    data: bytes | None = None
    url: str | None = None


class Envelope(BaseModel):
    """Unified inbound message, produced by every channel adapter."""

    user_id: str
    channel: str
    text: str = ""
    attachments: list[Attachment] = Field(default_factory=list)
    locale: str = "ru"
    ts: str | None = None


class Profile(BaseModel):
    """Authenticated user profile — the ONLY source of the access filter."""

    user_id: str
    department: str | None = None
    roles: list[str] = Field(default_factory=list)

    # The caller's OWN credential for the target product's API, carried inside
    # the signed token (claim ``product_token``). When present, an action calls
    # the product as this person rather than through one shared service key, so
    # the product's own permission checks still apply — the assistant can never
    # do more than the person asking could do by hand.
    #
    # Never serialised and never shown in a repr: it is a live credential, and
    # profiles end up in logs and action records.
    product_token: str | None = Field(default=None, repr=False, exclude=True)

    # Whatever else the product needs to know about this request, carried in the
    # signed token as the ``context`` claim: the workspace the person has open,
    # their tenant, their locale. A credential says *who* is asking; it does not
    # say *from where*, and products routinely keep that apart — in a cookie, a
    # header, a path segment. onbo calls the product server-to-server, so none
    # of the browser's own context arrives on its own, and the product quietly
    # falls back to a default.
    #
    # These values are usable anywhere a template is: `{account_id}` in an
    # action's url, body or query, and in `product.headers` in settings.yaml.
    # They come only from the signed token, never from the request body, so the
    # browser cannot make them up.
    context: dict[str, str] = Field(default_factory=dict)


class ActionType(str, Enum):
    profile_action = "profile_action"
    rag_query = "rag_query"
    about = "about"
    unknown = "unknown"


class ActionMode(str, Enum):
    chat = "chat"        # execute immediately
    confirm = "confirm"  # ask Ok/Cancel, execute only on Ok
    link = "link"        # sensitive: return a link, never execute in chat


# Values that mean "the message did not say" — however the model chose to spell
# it. Asked not to invent, a small model still answers `{"project_id": null}`
# rather than leaving the key out.
_NOT_A_VALUE = {"", "null", "none", "nil", "n/a", "undefined", "unknown"}


def drop_blank_entities(entities: Any) -> dict[str, Any]:
    """Throw away params the message never actually filled in.

    A null is "I don't know", not a value. Kept, it walks straight through the
    required-parameter check — the assistant then never asks the person, renders
    the word "None" into the confirmation, and sends it to the product as if it
    were real. So an empty value is treated exactly like an absent key.
    """
    if not isinstance(entities, dict):
        return {}
    return {
        str(name): value
        for name, value in entities.items()
        if value is not None
        and not (isinstance(value, str) and value.strip().lower() in _NOT_A_VALUE)
    }


class ClassifiedAction(BaseModel):
    """One item the classifier extracts from a (possibly multi-request) message."""

    type: ActionType
    action: str | None = None          # for profile_action: registry key
    query: str | None = None           # for rag_query: the question
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0

    @field_validator("entities", mode="before")
    @classmethod
    def _only_real_values(cls, value: Any) -> dict[str, Any]:
        # Done here rather than in the classifier: every source of entities —
        # the LLM, the regex fallback, a resumed slot-fill — passes through.
        return drop_blank_entities(value)


class Classification(BaseModel):
    actions: list[ClassifiedAction] = Field(default_factory=list)


class ResultStatus(str, Enum):
    done = "done"                    # executed
    answer = "answer"               # RAG / about answer
    needs_confirm = "needs_confirm"  # waiting for Ok/Cancel
    needs_input = "needs_input"      # missing required params
    link = "link"                    # sensitive -> link handed out
    dry_run = "dry_run"             # validated, but no product backend configured
    failed = "failed"               # not supported / error


class Link(BaseModel):
    """A place in the product the answer tells the reader to go.

    Kept apart from the answer text so a channel can render it as it likes —
    buttons in a widget, a list in Telegram — instead of parsing URLs out of prose.
    """

    title: str
    url: str


class ActionResult(BaseModel):
    status: ResultStatus
    action: str | None = None
    message: str = ""
    link_url: str | None = None
    confirm_prompt: str | None = None
    citations: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


class Response(BaseModel):
    """Single aggregated reply sent back to the channel."""

    text: str
    results: list[ActionResult] = Field(default_factory=list)
