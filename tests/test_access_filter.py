"""The crown-jewel security test: RAG access control must not leak.

We run a REAL Qdrant (qdrant-client ``:memory:`` local mode) so this exercises
the actual payload filter, not a mock. The filter is built ONLY from the
profile-derived AccessFilter — never from query text — so a support user must
never see accounting-only content.
"""
from __future__ import annotations

import pytest
from qdrant_client import AsyncQdrantClient

from onbo.config import Settings
from onbo.rag.qdrant_store import QdrantStore
from onbo.rag.store import AccessFilter, Chunk


@pytest.fixture
async def store():
    settings = Settings()
    settings.qdrant.collection = "test_access"
    st = QdrantStore(settings)
    client = AsyncQdrantClient(location=":memory:")
    st._client = client  # bypass the real server; local mode enforces filters

    # Distinct 4-dim vectors so we control ranking without embeddings.
    chunks = [
        Chunk(id="acc", text="acc-secret", department="accounting", roles=["accountant"]),
        Chunk(id="sup", text="sup-secret", department="support", roles=["support"]),
        Chunk(id="pub", text="public-info", department=None, roles=[]),
    ]
    vectors = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
    await st.upsert(chunks, vectors)
    return st


async def _texts(store, access):
    hits = await store.search([1, 1, 1, 1], access, limit=10)
    return {h.text for h in hits}


async def test_support_never_sees_accounting(store):
    seen = await _texts(store, AccessFilter(department="support", roles=["support"]))
    assert "acc-secret" not in seen          # no leak across departments
    assert "sup-secret" in seen
    assert "public-info" in seen             # public content is visible to all


async def test_accounting_sees_own_and_public_only(store):
    seen = await _texts(store, AccessFilter(department="accounting", roles=["accountant"]))
    assert seen == {"acc-secret", "public-info"}


async def test_empty_profile_sees_only_public(store):
    # A least-privilege profile (no department) must still only get public docs.
    seen = await _texts(store, AccessFilter(department=None, roles=[]))
    assert seen == {"public-info"}


async def test_right_department_wrong_role_is_blocked(store):
    # Same department, but the chunk restricts a role the user lacks -> hidden.
    seen = await _texts(store, AccessFilter(department="accounting", roles=["intern"]))
    assert "acc-secret" not in seen
    assert seen == {"public-info"}
