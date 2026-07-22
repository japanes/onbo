"""Admin API: the token gate protects /api/* while the page stays open."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from onbo.config import Settings
from onbo.kb import admin as admin_mod


@pytest.fixture
def client(monkeypatch):
    # Keep the DB out of it: stats() is stubbed so /api/stats needs no Postgres.
    monkeypatch.setattr(
        admin_mod.KnowledgeBaseAdmin,
        "stats",
        lambda self: {"db": False, "collections": 0, "documents": 0, "qa": 0},
    )
    from onbo.channels.admin_api import build_admin_router

    app = FastAPI()
    app.include_router(build_admin_router(Settings()))
    return TestClient(app)


def test_page_is_open_without_token(client, monkeypatch):
    monkeypatch.setenv("ONBO_ADMIN_TOKEN", "secret")
    r = client.get("/admin")
    assert r.status_code == 200
    assert "onbo" in r.text.lower()


def test_api_requires_token_when_set(client, monkeypatch):
    monkeypatch.setenv("ONBO_ADMIN_TOKEN", "secret")
    assert client.get("/admin/api/stats").status_code == 401           # no header
    assert client.get(
        "/admin/api/stats", headers={"X-Admin-Token": "wrong"}
    ).status_code == 401                                               # wrong token
    ok = client.get("/admin/api/stats", headers={"X-Admin-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json()["collections"] == 0


def test_api_is_open_when_token_unset(client, monkeypatch):
    monkeypatch.delenv("ONBO_ADMIN_TOKEN", raising=False)
    assert client.get("/admin/api/stats").status_code == 200


# -- video_url end-to-end through the admin API -----------------------------
# The KB backend (embeddings/Postgres) is heavy and out of scope here, so we
# stub the KnowledgeBaseAdmin methods and assert the router wires them right.


def test_post_qa_passes_video_url_and_links(client, monkeypatch):
    monkeypatch.delenv("ONBO_ADMIN_TOKEN", raising=False)
    seen = {}

    async def fake_add_qa(self, question, answer, collection, department=None,
                          roles=None, video_url=None, links=None):
        seen.update(question=question, video_url=video_url, collection=collection,
                    links=links)
        return 1

    monkeypatch.setattr(admin_mod.KnowledgeBaseAdmin, "add_qa", fake_add_qa)
    r = client.post("/admin/api/qa", json={
        "question": "Как добавить видео?", "answer": "Через админку.",
        "video_url": "/media/kb/1.mp4",
        "links": [{"title": "Админка", "url": "https://app/admin"}],
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["video_url"] == "/media/kb/1.mp4"
    assert seen["links"] == [{"title": "Админка", "url": "https://app/admin"}]


def test_patch_qa_success_and_404(client, monkeypatch):
    monkeypatch.delenv("ONBO_ADMIN_TOKEN", raising=False)
    patched = {}

    async def fake_update_qa(self, qa_id, **fields):
        patched[qa_id] = fields
        return qa_id == 1  # only #1 exists

    monkeypatch.setattr(admin_mod.KnowledgeBaseAdmin, "update_qa", fake_update_qa)

    ok = client.patch("/admin/api/qa/1", json={"video_url": "/media/kb/1.mp4"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    # exclude_unset: only the field we sent reaches the backend.
    assert patched[1] == {"video_url": "/media/kb/1.mp4"}

    missing = client.patch("/admin/api/qa/999", json={"answer": "нов."})
    assert missing.status_code == 404


def test_list_qa_exposes_video_url(client, monkeypatch):
    monkeypatch.delenv("ONBO_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(
        admin_mod.KnowledgeBaseAdmin, "list_qa",
        lambda self, collection=None: [{
            "id": 1, "collection": "common", "question": "Q", "answer": "A",
            "video_url": "/media/kb/1.mp4", "department": None, "roles": [],
        }],
    )
    r = client.get("/admin/api/qa")
    assert r.status_code == 200
    assert r.json()[0]["video_url"] == "/media/kb/1.mp4"
