"""Postgres access via SQLAlchemy — the canonical, editable store.

Users, action logs and knowledge-base content live here; Qdrant is only a
derived search index rebuilt from this data. Requires the ``state``/``kb`` extra.
"""
from __future__ import annotations

from contextlib import contextmanager
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


@contextmanager
def session_scope():
    """Transactional session: commit on success, roll back on error, always close."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _load_all_models() -> None:
    """Import every model module so ``Base.metadata`` knows all tables."""
    from ..kb import models as _kb_models  # noqa: F401
    from . import models as _state_models  # noqa: F401


def init_db() -> None:
    """Create tables from the ORM metadata (dev convenience)."""
    _load_all_models()
    Base.metadata.create_all(get_engine())  # type: ignore[union-attr]


def db_available() -> bool:
    """True if SQLAlchemy is installed and the Postgres server accepts a connection."""
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
