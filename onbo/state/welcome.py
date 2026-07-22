"""First-contact tracking: has this user already been proactively welcomed?

The Postgres ``app_user.welcomed_at`` column is canonical when the DB is up; the
``Session`` (Redis / in-memory) is the db-less fallback so the skeleton still
greets each user exactly once without Postgres. The column is added to pre-existing
databases by the best-effort migrations in ``state/db.py`` (no Alembic).
"""
from __future__ import annotations

from ..config import Settings
from .session import Session

def _ensure_column() -> None:
    """Make sure ``app_user.welcomed_at`` exists (see state/db.py::_MIGRATIONS)."""
    try:
        from .db import init_db

        init_db()
    except Exception:
        pass


async def is_welcomed(user_id: str, settings: Settings, session: Session) -> bool:
    """True if the user has already received the proactive welcome."""
    from .db import db_available, session_scope
    from .models import User

    if User is not None and db_available():
        _ensure_column()
        try:
            with session_scope() as db:
                row = db.get(User, user_id)
                return bool(row is not None and row.welcomed_at is not None)
        except Exception:
            pass  # fall through to the db-less marker
    return await session.is_welcomed(user_id)


async def mark_welcomed(user_id: str, settings: Settings, session: Session) -> None:
    """Record that the user has now been welcomed (idempotent)."""
    from .db import db_available, init_db, session_scope
    from .models import User

    if User is not None and db_available():
        _ensure_column()
        try:
            from datetime import datetime, timezone

            init_db()
            with session_scope() as db:
                row = db.get(User, user_id)
                if row is None:
                    row = User(user_id=user_id)
                    db.add(row)
                row.welcomed_at = datetime.now(timezone.utc)
            return
        except Exception:
            pass  # fall through to the db-less marker
    await session.mark_welcomed(user_id)
