"""Resolve a user_id into a Profile (department / roles).

This is the SINGLE source of the RAG access filter: retrieval visibility is
derived from the authenticated profile here, never from the user's text and
never from the LLM. See core/router.py and rag/retriever.py.
"""
from __future__ import annotations

from ..config import Settings
from ..core.schemas import Profile

# Demo directory so the skeleton is testable end-to-end without a real user DB.
# Real deployments resolve the profile from Postgres (see _lookup_db).
_DEMO_USERS: dict[str, dict] = {
    "acc1": {"department": "accounting", "roles": ["accountant"]},
    "sup1": {"department": "support", "roles": ["support"]},
    "admin": {"department": "it", "roles": ["admin"]},
}


def _lookup_db(user_id: str, settings: Settings) -> Profile | None:
    """Look up a profile in Postgres. Stubbed until the users table exists."""
    # TODO: query state.db for the user's department/roles.
    return None


async def resolve_profile(user_id: str, settings: Settings) -> Profile:
    profile = _lookup_db(user_id, settings)
    if profile is not None:
        return profile
    demo = _DEMO_USERS.get(user_id)
    if demo is not None:
        return Profile(user_id=user_id, **demo)
    # Unknown user: least-privilege default (only public / `about` content).
    return Profile(user_id=user_id, department=None, roles=["employee"])
