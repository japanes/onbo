"""CLI-only generator: scan a target project and draft an actions.yaml.

This is NOT part of the runtime. It inspects a project's API routes / settings
pages / docs and asks the LLM to draft a registry. The output is a DRAFT that a
human must review — generated actions touch passwords and personal data, so it
must never reach production unreviewed.
"""
from __future__ import annotations

import os

from ..config import Settings, load_settings
from ..core.llm import LLM, LLMUnavailable

_HINT_FILES = ("routes", "urls", "api", "settings", "views", "controllers")

_PROMPT = """You are drafting an onboarding assistant action registry.
Given source-code excerpts from a target product, propose a YAML `actions:`
mapping. For each action set: description, mode (chat|confirm|link), params, and
handler path. Rules:
- Sensitive operations (password, personal data, payment) -> mode: link, sensitive: true.
- Reversible-but-important operations (email, phone) -> mode: confirm.
- Low-risk operations (language, theme) -> mode: chat.
Return YAML only."""


def _collect_excerpts(project_path: str, max_bytes: int = 40_000) -> str:
    excerpts: list[str] = []
    budget = max_bytes
    for root, _dirs, names in os.walk(project_path):
        for name in names:
            if not name.endswith((".py", ".ts", ".js")):
                continue
            if not any(hint in name.lower() or hint in root.lower() for hint in _HINT_FILES):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read(4_000)
            except OSError:
                continue
            excerpts.append(f"# {path}\n{text}")
            budget -= len(text)
            if budget <= 0:
                return "\n\n".join(excerpts)
    return "\n\n".join(excerpts)


async def scan_project(project_path: str, settings: Settings | None = None) -> str:
    """Return a draft actions.yaml (as text) for human review."""
    settings = settings or load_settings()
    excerpts = _collect_excerpts(project_path)
    if not excerpts.strip():
        return "# No API/settings/route files found to scan.\nactions: {}\n"
    llm = LLM(settings)
    try:
        return await llm.complete(
            [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": excerpts},
            ],
            temperature=0.0,
        )
    except LLMUnavailable as exc:
        return f"# LLM unavailable: {exc}\nactions: {{}}\n"
