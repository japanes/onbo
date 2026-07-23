"""The command catalogue as a searchable index.

Without this, every single message carries every single command into the prompt:
34 actions with all their parameters is thousands of tokens per turn, paid on
«привет» as surely as on «удали проект». It costs money, it costs latency, and it
costs accuracy — the longer the list, the more willingly a model picks the wrong
line out of it.

So the catalogue is embedded once and searched per message. What gets indexed is
deliberately wider than what a person is shown: the name, the human description,
the parameters' descriptions, and the ``keywords``/``examples`` phrasings from
actions.yaml. All of that only has to make the command *findable*; the prompt
still receives the short description.

The index is replaced wholesale rather than patched (see ``reindex_actions``),
and it is stamped with a fingerprint of actions.yaml so a startup can tell that
the file moved on without it — a command that exists in the file but not in the
index is exactly the kind of bug that gets debugged for an hour.
"""
from __future__ import annotations

import hashlib
import json

from ...config import Settings
from ...rag.store import ACTION, Chunk


def _text_of(spec) -> str:
    """Everything a person might say to mean this command, in one string."""
    parts = [spec.description or spec.name, spec.name]
    parts += list(getattr(spec, "keywords", None) or [])
    parts += list(getattr(spec, "examples", None) or [])
    for param_name, param in (getattr(spec, "params", None) or {}).items():
        parts.append(param.description or param_name)
    return "\n".join(p for p in parts if p)


def action_chunks(specs: dict, fingerprint: str = "") -> list[Chunk]:
    """One chunk per command, carrying its access tags into Qdrant.

    ``department``/``roles`` are copied verbatim, so the shortlist is filtered
    server-side by the same rule as the knowledge base instead of relying on a
    later check in Python.
    """
    return [
        Chunk(
            id=f"action::{name}",
            text=_text_of(spec),
            kind=ACTION,
            source=name,               # what the caller wants back: the command name
            department=getattr(spec, "department", None),
            roles=list(getattr(spec, "roles", None) or []),
            meta={"fingerprint": fingerprint} if fingerprint else {},
        )
        for name, spec in specs.items()
    ]


def fingerprint(specs: dict) -> str:
    """A short hash of what is indexable about the current catalogue.

    Built from the specs rather than the file bytes: a comment or a reordering
    should not trigger a reindex, and a change that reaches the index always
    should.
    """
    payload = json.dumps(
        {name: _text_of(spec) for name, spec in sorted(specs.items())},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def reindex_actions(settings: Settings, specs: dict) -> int:
    """Rebuild the command index from ``specs``. Returns how many were indexed.

    Deletes first: an upsert cannot remove a command that was dropped from
    actions.yaml, and a command that still answers after being deleted is worse
    than one that is missing.
    """
    from ...rag.embeddings import Embedder
    from ...rag.qdrant_store import QdrantStore

    store = QdrantStore(settings)
    await store.delete_kind(ACTION)
    chunks = action_chunks(specs, fingerprint(specs))
    if not chunks:
        return 0
    vectors = Embedder(settings).encode([c.text for c in chunks])
    await store.upsert(chunks, vectors)
    return len(chunks)


async def reindex_if_stale(settings: Settings, specs: dict) -> int:
    """Reindex only when the stored fingerprint does not match the current one.

    Called on startup. Any failure here is swallowed by the caller: a missing
    index degrades to the full catalogue in the prompt, which is slow and
    expensive but still correct — refusing to boot would not be.
    """
    from ...rag.qdrant_store import QdrantStore

    stored = await QdrantStore(settings).payload_sample(ACTION)
    if stored is not None and stored.get("fingerprint") == fingerprint(specs):
        return 0
    return await reindex_actions(settings, specs)
