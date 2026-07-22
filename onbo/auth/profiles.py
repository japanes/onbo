"""Resolve a user_id into a Profile (department / roles).

This is the SINGLE source of the RAG access filter: retrieval visibility is
derived from the authenticated profile here, never from the user's text and
never from the LLM. See core/router.py and rag/retriever.py.
"""
from __future__ import annotations

from ..config import Settings
from ..core.schemas import Profile

# Least-privilege profile for anyone the directory doesn't know: no department,
# so only content tagged as public reaches them.
_DEFAULT_ROLES = ["employee"]


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


def upsert_users(users: list[dict]) -> int:
    """Write directory entries into the users table.

    Each item: ``{user_id, department?, roles?}``. Idempotent by ``user_id``, so
    re-importing an updated file just rewrites the department and roles.
    Returns 0 when there is no reachable Postgres.
    """
    from ..state.db import db_available, init_db, session_scope
    from ..state.models import User

    if User is None or not db_available():
        return 0
    init_db()
    count = 0
    with session_scope() as session:
        for item in users:
            user_id = str(item["user_id"])
            row = session.get(User, user_id)
            if row is None:
                row = User(user_id=user_id)
                session.add(row)
            row.department = item.get("department")
            row.roles = list(item.get("roles") or [])
            count += 1
    return count


def import_users(path: str) -> int:
    """Load a directory file (``users:`` list) into the users table."""
    import yaml

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return upsert_users(data.get("users", []))


async def resolve_profile(user_id: str, settings: Settings) -> Profile:
    profile = _lookup_db(user_id, settings)
    if profile is not None:
        return profile
    return Profile(user_id=user_id, department=None, roles=list(_DEFAULT_ROLES))
