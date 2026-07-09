"""Postgres access via SQLAlchemy — the canonical, editable store.

Users, action logs and knowledge-base content live here; Qdrant is only a
derived search index rebuilt from this data. Requires the ``state``/``kb`` extra.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import load_settings

try:
    from sqlalchemy.orm import DeclarativeBase

    class Base(DeclarativeBase):
        """Declarative base shared by all ORM models."""

except ImportError:  # pragma: no cover - depends on extras
    # Placeholder so modules can still be imported without SQLAlchemy installed.
    Base = object  # type: ignore[assignment,misc]


@lru_cache(maxsize=1)
def get_engine(dsn: str | None = None):
    from sqlalchemy import create_engine

    return create_engine(dsn or load_settings().postgres_dsn, future=True)


def get_session():
    from sqlalchemy.orm import Session

    return Session(bind=get_engine(), future=True)


def init_db() -> None:
    """Create tables from the ORM metadata (dev convenience)."""
    Base.metadata.create_all(get_engine())  # type: ignore[union-attr]
