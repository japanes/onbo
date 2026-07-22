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
    """Look up a profile in the Postgres users table, if the DB is reachable."""
    from ..state.db import db_available, session_scope
    from ..state.models import User

    if User is None or not db_available():
        return None
    try:
        with session_scope() as session:
            row = session.get(User, user_id)
            if row is None:
                return None
            return Profile(user_id=row.user_id, department=row.department, roles=list(row.roles or []))
    except Exception:
        # Never let an auth-store hiccup widen access — fall through to defaults.
        return None


def seed_demo_users(settings: Settings) -> int:
    """Upsert the demo directory into the users table (dev convenience)."""
    from ..state.db import db_available, init_db, session_scope
    from ..state.models import User

    if User is None or not db_available():
        return 0
    init_db()
    with session_scope() as session:
        for user_id, attrs in _DEMO_USERS.items():
            row = session.get(User, user_id)
            if row is None:
                row = User(user_id=user_id)
                session.add(row)
            row.department = attrs["department"]
            row.roles = attrs["roles"]
    return len(_DEMO_USERS)


async def resolve_profile(user_id: str, settings: Settings) -> Profile:
    profile = _lookup_db(user_id, settings)
    if profile is not None:
        return profile
    demo = _DEMO_USERS.get(user_id)
    if demo is not None:
        return Profile(user_id=user_id, **demo)
    # Unknown user: least-privilege default (only public / `about` content).
    return Profile(user_id=user_id, department=None, roles=["employee"])
