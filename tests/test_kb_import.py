"""`onbo kb import`: load a YAML file with a `qa:` list into the KB.

The actual indexing (embeddings + Qdrant/Postgres) is heavy and covered
elsewhere; here we stub ``add_qa`` and assert seed() parses the file and threads
every field — including ``video_url`` and ``links`` — through per item.
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
                    "links": [{"title": "Возвраты", "url": "https://app/refunds"}],
                },
            ]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    admin = KnowledgeBaseAdmin(Settings())
    calls: list[tuple] = []

    async def fake_add_qa(question, answer, collection, department=None,
                          roles=None, video_url=None, links=None):
        calls.append(
            (question, answer, collection, department, roles, video_url, links)
        )
        return 1

    monkeypatch.setattr(admin, "add_qa", fake_add_qa)

    n = await admin.import_qa(str(faq))

    assert n == 2
    assert calls[0] == ("Q1", "A1", "common", None, None, None, None)
    assert calls[1] == (
        "Q2", "A2", "accounting", "accounting", ["accountant"], "/media/kb/q2.mp4",
        [{"title": "Возвраты", "url": "https://app/refunds"}],
    )


async def test_import_missing_file_raises(tmp_path):
    """A typo in the path must be reported, not silently import nothing."""
    import pytest

    admin = KnowledgeBaseAdmin(Settings())
    with pytest.raises(FileNotFoundError):
        await admin.import_qa(str(tmp_path / "nope.yaml"))
