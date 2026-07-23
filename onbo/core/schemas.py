"""Shared data contracts (pydantic) used across the whole pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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

    # Extra headers the product needs to answer the same way it answers the
    # browser (claim ``product_headers``). A credential says *who* is asking;
    # some products also need *in which context* — active workspace, tenant,
    # locale — and keep that outside the credential, in a cookie or a header the
    # browser sends. Without it the product silently picks a default context and
    # the action lands in the wrong place. Signed by your backend, so the
    # browser cannot inject headers of its own.
    product_headers: dict[str, str] = Field(
        default_factory=dict, repr=False, exclude=True
    )


class ActionType(str, Enum):
    profile_action = "profile_action"
    rag_query = "rag_query"
    about = "about"
    unknown = "unknown"


class ActionMode(str, Enum):
    chat = "chat"        # execute immediately
    confirm = "confirm"  # ask Ok/Cancel, execute only on Ok
    link = "link"        # sensitive: return a link, never execute in chat


class ClassifiedAction(BaseModel):
    """One item the classifier extracts from a (possibly multi-request) message."""

    type: ActionType
    action: str | None = None          # for profile_action: registry key
    query: str | None = None           # for rag_query: the question
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0


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
