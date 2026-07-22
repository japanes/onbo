"""Knowledge-base ORM models — Postgres is the canonical, editable source.

Qdrant is only a derived index rebuilt from these tables, so documents / Q&A /
collections can be edited, deleted and reindexed without data loss. Access is
governed per collection (default) and can be overridden per document.
Requires the ``kb`` extra (SQLAlchemy).
"""
from __future__ import annotations

from ..state.db import Base

try:
    from sqlalchemy import ForeignKey, JSON, String, Text
    from sqlalchemy.orm import Mapped, mapped_column, relationship

    class Collection(Base):
        __tablename__ = "kb_collection"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(128), unique=True)
        # Default access inherited by documents in this collection.
        department: Mapped[str | None] = mapped_column(String(64), nullable=True)
        roles: Mapped[list] = mapped_column(JSON, default=list)

        documents: Mapped[list["Document"]] = relationship(back_populates="collection")
        qa_pairs: Mapped[list["QAPair"]] = relationship(back_populates="collection")

    class Document(Base):
        __tablename__ = "kb_document"

        id: Mapped[int] = mapped_column(primary_key=True)
        collection_id: Mapped[int] = mapped_column(ForeignKey("kb_collection.id"))
        source: Mapped[str] = mapped_column(String(512))  # file path or URL
        title: Mapped[str | None] = mapped_column(String(256), nullable=True)
        body: Mapped[str] = mapped_column(Text)
        # Optional per-document override of the collection's access tags.
        department: Mapped[str | None] = mapped_column(String(64), nullable=True)
        roles: Mapped[list | None] = mapped_column(JSON, nullable=True)

        collection: Mapped["Collection"] = relationship(back_populates="documents")

    class QAPair(Base):
        __tablename__ = "kb_qa"

        id: Mapped[int] = mapped_column(primary_key=True)
        collection_id: Mapped[int] = mapped_column(ForeignKey("kb_collection.id"))
        question: Mapped[str] = mapped_column(Text)  # embedded for retrieval
        answer: Mapped[str] = mapped_column(Text)
        # Optional walkthrough video attached to this pair (served from /media).
        video_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
        department: Mapped[str | None] = mapped_column(String(64), nullable=True)
        roles: Mapped[list | None] = mapped_column(JSON, nullable=True)

        collection: Mapped["Collection"] = relationship(back_populates="qa_pairs")

except ImportError:  # pragma: no cover - depends on the kb extra
    Collection = Document = QAPair = None  # type: ignore[assignment,misc]
