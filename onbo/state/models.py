"""Runtime ORM models kept in Postgres: users and the action audit log.

Users are the canonical source of the RAG access filter (department / roles),
resolved by ``auth/profiles.py``. The action log records every profile action
that was executed (or handed out as a link), for audit. Requires the ``kb``/
``state`` extra (SQLAlchemy).
"""
from __future__ import annotations

from .db import Base

try:
    from datetime import datetime, timezone

    from sqlalchemy import JSON, DateTime, String, Text
    from sqlalchemy.orm import Mapped, mapped_column

    def _utcnow() -> "datetime":
        return datetime.now(timezone.utc)

    class User(Base):
        __tablename__ = "app_user"

        # The channel-facing id (Telegram id, web session user, ...).
        user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
        department: Mapped[str | None] = mapped_column(String(64), nullable=True)
        roles: Mapped[list] = mapped_column(JSON, default=list)
        # First-contact marker: set once the proactive welcome has been delivered.
        welcomed_at: Mapped["datetime | None"] = mapped_column(
            DateTime(timezone=True), nullable=True
        )

    class ActionLog(Base):
        __tablename__ = "action_log"

        id: Mapped[int] = mapped_column(primary_key=True)
        user_id: Mapped[str] = mapped_column(String(128))
        action: Mapped[str] = mapped_column(String(128))
        status: Mapped[str] = mapped_column(String(32))
        detail: Mapped[str | None] = mapped_column(Text, nullable=True)
        created_at: Mapped["datetime"] = mapped_column(DateTime(timezone=True), default=_utcnow)

except ImportError:  # pragma: no cover - depends on the state extra
    User = ActionLog = None  # type: ignore[assignment,misc]
