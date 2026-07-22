"""`onbo kb import` / seed(path): load a seed_faq.yaml-shaped file into the KB.

The actual indexing (embeddings + Qdrant/Postgres) is heavy and covered
elsewhere; here we stub ``add_qa`` and assert seed() parses the file and threads
every field — including the new ``video_url`` — through per item.
"""
from __future__ import annotations

import yaml

from onbo.config import Settings
from onbo.kb.admin import KnowledgeBaseAdmin


async def test_import_reads_file_and_threads_all_fields(tmp_path, monkeypatch):
    faq = tmp_path / "faq.yaml"
    faq.write_text(
        yaml.safe_dump(
            {"qa": [
                {"question": "Q1", "answer": "A1"},
                {
                    "question": "Q2", "answer": "A2",
                    "collection": "accounting", "department": "accounting",
                    "roles": ["accountant"], "video_url": "/media/kb/q2.mp4",
                },
            ]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    admin = KnowledgeBaseAdmin(Settings())
    calls: list[tuple] = []

    async def fake_add_qa(question, answer, collection,
                          department=None, roles=None, video_url=None):
        calls.append((question, answer, collection, department, roles, video_url))
        return 1

    monkeypatch.setattr(admin, "add_qa", fake_add_qa)

    n = await admin.seed(str(faq))

    assert n == 2
    assert calls[0] == ("Q1", "A1", "common", None, None, None)
    assert calls[1] == (
        "Q2", "A2", "accounting", "accounting", ["accountant"], "/media/kb/q2.mp4",
    )


async def test_import_missing_file_returns_zero(tmp_path):
    admin = KnowledgeBaseAdmin(Settings())
    assert await admin.seed(str(tmp_path / "nope.yaml")) == 0
