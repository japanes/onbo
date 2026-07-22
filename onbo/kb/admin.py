"""KB management: CRUD for collections / documents / Q&A, plus (re)indexing.

Postgres (``kb.models``) is the canonical, editable store; Qdrant is a derived
index rebuilt from it. Every write persists to Postgres first (idempotent by
``collection + source`` / ``collection + question``), then upserts into Qdrant
with a stable id (``doc:{id}#{n}`` / ``qa:{id}``) so ``reindex`` is repeatable.

Exposed two ways over the same functions: the admin API (mounted under /admin by
the web channel) and the ``onbo kb ...`` CLI. When Postgres is unavailable the
writes fall back to index-only mode so the tool still runs in bare dev setups.
"""
from __future__ import annotations

import os

from ..config import Settings
from ..rag.store import Chunk
from ..state.db import db_available, init_db, session_scope
from .index import Indexer
from .models import Collection, Document, QAPair
from .sources.files import FileSource
from .sources.website import WebsiteSource


class KnowledgeBaseAdmin:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._indexer = Indexer(settings)
        self._schema_ready = False

    # -- schema / collections -------------------------------------------------

    # Best-effort column additions for tables that already exist (no Alembic).
    # Each is idempotent (IF NOT EXISTS) and tolerant of an old server that
    # doesn't support the syntax — the feature just stays unavailable there.
    _MIGRATIONS = (
        "ALTER TABLE kb_qa ADD COLUMN IF NOT EXISTS video_url VARCHAR(512)",
    )

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        init_db()  # create_all is idempotent
        from sqlalchemy import text

        for stmt in self._MIGRATIONS:
            try:
                with session_scope() as session:
                    session.execute(text(stmt))
            except Exception:  # pragma: no cover - old/limited server; skip silently
                pass
        self._schema_ready = True

    @staticmethod
    def _get_or_create_collection(session, name, department, roles) -> Collection:
        from sqlalchemy import select

        col = session.execute(select(Collection).where(Collection.name == name)).scalar_one_or_none()
        if col is None:
            col = Collection(name=name, department=department, roles=roles or [])
            session.add(col)
            session.flush()
        return col

    @staticmethod
    def _effective(row_dept, row_roles, col: Collection):
        """A row inherits the collection's access tags unless it overrides them."""
        dept = row_dept if row_dept is not None else col.department
        roles = row_roles if row_roles is not None else (col.roles or [])
        return dept, roles

    # -- writes ---------------------------------------------------------------

    async def add_doc(
        self,
        path: str,
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
    ) -> int:
        """Ingest files or a URL into a collection with access tags."""
        source = WebsiteSource(path) if path.startswith(("http://", "https://")) else FileSource(path)
        docs = source.fetch()
        if not db_available():  # bare dev setup: index only, no persistence
            return await self._indexer.index_documents(docs, collection, department, roles)

        self._ensure_schema()
        from sqlalchemy import select

        chunks: list[Chunk] = []
        with session_scope() as session:
            col = self._get_or_create_collection(session, collection, department, roles)
            for doc in docs:
                row = session.execute(
                    select(Document).where(
                        Document.collection_id == col.id, Document.source == doc.source
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = Document(collection_id=col.id, source=doc.source)
                    session.add(row)
                row.title = doc.title
                row.body = doc.body
                row.department = department
                row.roles = roles
                session.flush()
                dept, eff_roles = self._effective(department, roles, col)
                chunks += self._indexer.build_doc_chunks(
                    doc.body, f"doc:{row.id}", doc.source, collection, dept, eff_roles
                )
        return await self._indexer.upsert_chunks(chunks)

    async def add_qa(
        self,
        question: str,
        answer: str,
        collection: str,
        department: str | None = None,
        roles: list[str] | None = None,
        video_url: str | None = None,
    ) -> int:
        if not db_available():
            return await self._indexer.index_qa(
                question, answer, collection, department, roles, video_url
            )

        self._ensure_schema()
        from sqlalchemy import select

        with session_scope() as session:
            col = self._get_or_create_collection(session, collection, department, roles)
            row = session.execute(
                select(QAPair).where(
                    QAPair.collection_id == col.id, QAPair.question == question
                )
            ).scalar_one_or_none()
            if row is None:
                row = QAPair(collection_id=col.id, question=question)
                session.add(row)
            row.answer = answer
            row.department = department
            row.roles = roles
            row.video_url = video_url
            session.flush()
            dept, eff_roles = self._effective(department, roles, col)
            chunk = Chunk(
                id=f"qa:{row.id}",
                text=answer,
                source=f"Q&A: {question}",
                is_qa=True,
                department=dept,
                roles=eff_roles,
                collection=collection,
                video_url=video_url,
            )
        return await self._indexer.upsert_qa_chunk(question, chunk)

    async def update_qa(self, qa_id: int, **fields) -> bool:
        """Patch any subset of a Q&A row, then re-upsert its ``qa:{id}`` point.

        Accepts question / answer / collection / department / roles / video_url.
        Returns False if the row (or DB) is missing (the API maps that to 404).
        The stable point id makes the Qdrant re-index an idempotent upsert.
        """
        if not db_available():
            return False
        self._ensure_schema()

        with session_scope() as session:
            row = session.get(QAPair, qa_id)
            if row is None:
                return False
            if fields.get("collection"):
                col = self._get_or_create_collection(
                    session, fields["collection"], fields.get("department"), fields.get("roles")
                )
                row.collection_id = col.id
            for key in ("question", "answer", "department", "roles", "video_url"):
                if key in fields:
                    setattr(row, key, fields[key])
            session.flush()
            col = session.get(Collection, row.collection_id)
            dept, eff_roles = self._effective(row.department, row.roles, col)
            question = row.question
            chunk = Chunk(
                id=f"qa:{row.id}",
                text=row.answer,
                source=f"Q&A: {row.question}",
                is_qa=True,
                department=dept,
                roles=eff_roles,
                collection=col.name,
                video_url=row.video_url,
            )
        await self._indexer.upsert_qa_chunk(question, chunk)
        return True

    async def seed(self, path: str | None = None) -> int:
        """Load Q&A pairs from a ``seed_faq.yaml``-shaped file (``qa:`` list).

        Without ``path`` it loads config/seed_faq.yaml so the KB isn't empty out
        of the box; with a path it imports an arbitrary file (``onbo kb import``).
        Each item: ``{question, answer, collection?, department?, roles?, video_url?}``.
        """
        import yaml

        from ..config import config_dir

        if path is None:
            path = os.path.join(config_dir(), "seed_faq.yaml")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        count = 0
        for item in data.get("qa", []):
            count += await self.add_qa(
                item["question"],
                item["answer"],
                item.get("collection", "common"),
                item.get("department"),
                item.get("roles"),
                item.get("video_url"),
            )
        return count

    async def reindex(self) -> int:
        """Rebuild the Qdrant index from the canonical Postgres content."""
        if not db_available():
            raise RuntimeError(
                "reindex requires Postgres (the `kb` extra + a running server)."
            )
        self._ensure_schema()
        from sqlalchemy import select

        await self._indexer.reset()

        doc_chunks: list[Chunk] = []
        qa_items: list[tuple[str, Chunk]] = []
        with session_scope() as session:
            cols = {c.id: c for c in session.execute(select(Collection)).scalars()}
            for doc in session.execute(select(Document)).scalars():
                col = cols[doc.collection_id]
                dept, roles = self._effective(doc.department, doc.roles, col)
                doc_chunks += self._indexer.build_doc_chunks(
                    doc.body, f"doc:{doc.id}", doc.source, col.name, dept, roles
                )
            for qa in session.execute(select(QAPair)).scalars():
                col = cols[qa.collection_id]
                dept, roles = self._effective(qa.department, qa.roles, col)
                qa_items.append((
                    qa.question,
                    Chunk(
                        id=f"qa:{qa.id}",
                        text=qa.answer,
                        source=f"Q&A: {qa.question}",
                        is_qa=True,
                        department=dept,
                        roles=roles,
                        collection=col.name,
                        video_url=qa.video_url,
                    ),
                ))

        total = await self._indexer.upsert_chunks(doc_chunks)
        for question, chunk in qa_items:
            total += await self._indexer.upsert_qa_chunk(question, chunk)
        return total

    # -- reads (for the admin API / panel) ------------------------------------

    def list_collections(self) -> list[dict]:
        if not db_available():
            return []
        self._ensure_schema()
        from sqlalchemy import select

        with session_scope() as session:
            return [
                {"id": c.id, "name": c.name, "department": c.department, "roles": c.roles or []}
                for c in session.execute(select(Collection).order_by(Collection.name)).scalars()
            ]

    def list_documents(self, collection: str | None = None) -> list[dict]:
        if not db_available():
            return []
        self._ensure_schema()
        from sqlalchemy import select

        with session_scope() as session:
            stmt = select(Document, Collection).join(Collection, Document.collection_id == Collection.id)
            if collection:
                stmt = stmt.where(Collection.name == collection)
            return [
                {
                    "id": doc.id,
                    "collection": col.name,
                    "source": doc.source,
                    "title": doc.title,
                    "chars": len(doc.body or ""),
                    "department": doc.department if doc.department is not None else col.department,
                }
                for doc, col in session.execute(stmt.order_by(Document.id)).all()
            ]

    def list_qa(self, collection: str | None = None) -> list[dict]:
        if not db_available():
            return []
        self._ensure_schema()
        from sqlalchemy import select

        with session_scope() as session:
            stmt = select(QAPair, Collection).join(Collection, QAPair.collection_id == Collection.id)
            if collection:
                stmt = stmt.where(Collection.name == collection)
            return [
                {
                    "id": qa.id,
                    "collection": col.name,
                    "question": qa.question,
                    "answer": qa.answer,
                    "video_url": qa.video_url,
                    "department": qa.department if qa.department is not None else col.department,
                    "roles": qa.roles if qa.roles is not None else (col.roles or []),
                }
                for qa, col in session.execute(stmt.order_by(QAPair.id)).all()
            ]

    def delete_qa(self, qa_id: int) -> bool:
        if not db_available():
            return False
        self._ensure_schema()
        with session_scope() as session:
            row = session.get(QAPair, qa_id)
            if row is None:
                return False
            session.delete(row)
        return True

    def stats(self) -> dict:
        return {
            "db": db_available(),
            "collections": len(self.list_collections()),
            "documents": len(self.list_documents()),
            "qa": len(self.list_qa()),
        }
