"""Machine-readable manifest (``llm.json``) for external LLM agents.

A public, safe-to-expose summary of what this assistant offers: the chat
endpoint, the *public* Q&A (empty ``department``/``roles`` only — private
knowledge never leaves), and the *public* actions/pipelines (name, description,
mode, params, plus ``link_url`` for sensitive link-actions). Internal wiring is
deliberately omitted: the ``api:`` blocks that describe how to call the target
backend, and the pipeline step lists, stay inside the box.
"""
from __future__ import annotations

from ..config import Settings
from ..handlers.actions.registry import load_action_specs, load_pipeline_specs
from ..kb.admin import KnowledgeBaseAdmin


def _is_public(department, roles) -> bool:
    """Public = no department restriction and no role restriction."""
    return not department and not roles


def _param(spec) -> dict:
    out: dict = {"type": spec.type, "required": spec.required}
    if spec.values:
        out["values"] = list(spec.values)
    if spec.description:
        out["description"] = spec.description
    return out


def _spec_entry(spec) -> dict:
    """Serialise an action/pipeline for external consumers (no ``api:``/steps)."""
    entry: dict = {
        "name": spec.name,
        "description": spec.description,
        "mode": spec.mode.value,
        "params": {name: _param(p) for name, p in spec.params.items()},
    }
    if spec.link_url:
        entry["link_url"] = spec.link_url
    return entry


def build_llm_manifest(
    settings: Settings,
    *,
    actions: dict | None = None,
    pipelines: dict | None = None,
    kb_admin=None,
) -> dict:
    """Assemble the public manifest dict served at ``GET /llm.json``.

    The ``actions``/``pipelines``/``kb_admin`` arguments are injection seams for
    tests; in production they default to the loaded config and a real KB admin.
    """
    if actions is None:
        actions = load_action_specs()
    if pipelines is None:
        pipelines = load_pipeline_specs(actions)
    if kb_admin is None:
        kb_admin = KnowledgeBaseAdmin(settings)

    try:
        qa_rows = kb_admin.list_qa()
    except Exception:  # no DB / index -> empty KB, still a valid manifest
        qa_rows = []

    qa = [
        {
            "collection": row["collection"],
            "question": row["question"],
            "answer": row["answer"],
            "video_url": row.get("video_url"),
        }
        for row in qa_rows
        if _is_public(row.get("department"), row.get("roles"))
    ]

    return {
        "product": {
            "name": settings.product.name,
            "description": settings.product.description,
        },
        "chat_endpoint": "/chat",
        "qa": qa,
        "actions": [
            _spec_entry(s) for s in actions.values() if _is_public(s.department, s.roles)
        ],
        "pipelines": [
            _spec_entry(s) for s in pipelines.values() if _is_public(s.department, s.roles)
        ],
    }
