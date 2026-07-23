"""The command catalogue as a searchable index (kinds, staleness, degradation).

Two things live in one Qdrant collection now: the knowledge base (``content``)
and the commands themselves (``action``). The first half of this file runs a
REAL Qdrant (``:memory:`` local mode) to prove they never turn up in each
other's results — an answer built out of command descriptions is nonsense, and a
command picked out of a help article is worse.

The second half covers the classifier's side of it: the shortlist is a
suggestion, and every way of failing to get one falls back to the full
catalogue.
"""
from __future__ import annotations

import pytest
from qdrant_client import AsyncQdrantClient

from onbo.config import Settings
from onbo.core.classifier import Classifier
from onbo.core.schemas import Envelope, Profile
from onbo.handlers.actions.index import (
    action_chunks,
    fingerprint,
    reindex_actions,
    reindex_if_stale,
)
from onbo.handlers.actions.registry import ActionSpec, ParamSpec
from onbo.rag.qdrant_store import QdrantStore
from onbo.rag.store import ACTION, CONTENT, AccessFilter, Chunk


@pytest.fixture
def profile():
    return Profile(user_id="u1", department="marketing", roles=["smm"])


def _specs(n: int = 3) -> dict:
    specs = {
        "delete_project": ActionSpec(
            name="delete_project",
            description="Удалить проект",
            keywords=["снести", "убрать"],
        ),
        "create_post": ActionSpec(
            name="create_post",
            description="Создать пост",
            params={"project_id": ParamSpec(required=True, description="в каком проекте")},
        ),
        "secret_action": ActionSpec(
            name="secret_action",
            description="Только для бухгалтерии",
            department="accounting",
        ),
    }
    for i in range(n - len(specs)):
        specs[f"filler_{i}"] = ActionSpec(name=f"filler_{i}", description=f"Действие {i}")
    return specs


# -- one collection, two kinds ----------------------------------------------


@pytest.fixture
async def store():
    settings = Settings()
    settings.qdrant.collection = "test_kinds"
    st = QdrantStore(settings)
    st._client = AsyncQdrantClient(location=":memory:")

    await st.upsert(
        [
            Chunk(id="doc", text="как оформить отпуск", kind=CONTENT),
            Chunk(id="legacy", text="старый чанк без kind"),   # written before kinds
            Chunk(id="action::delete_project", text="Удалить проект", kind=ACTION,
                  source="delete_project"),
        ],
        [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
    )
    return st


async def _texts(store, kind):
    hits = await store.search([1, 1, 1, 1], AccessFilter(), limit=10, kind=kind)
    return {h.text for h in hits}


async def test_a_knowledge_search_never_returns_a_command(store):
    assert await _texts(store, CONTENT) == {"как оформить отпуск", "старый чанк без kind"}


async def test_a_command_search_never_returns_knowledge(store):
    assert await _texts(store, ACTION) == {"Удалить проект"}


async def test_a_command_hit_carries_its_name(store):
    hits = await store.search([1, 1, 1, 1], AccessFilter(), limit=10, kind=ACTION)
    assert [h.source for h in hits] == ["delete_project"]


async def test_dropping_the_command_index_leaves_the_knowledge_base_alone(store):
    """A command deleted from actions.yaml has to stop being offered."""
    await store.delete_kind(ACTION)
    assert await _texts(store, ACTION) == set()
    assert len(await _texts(store, CONTENT)) == 2


async def test_the_fingerprint_travels_with_the_points(store):
    chunks = action_chunks(_specs(), "abc123")
    await store.upsert(chunks, [[0, 0, 0, 1]] * len(chunks))
    sample = await store.payload_sample(ACTION)
    assert sample["fingerprint"] == "abc123"
    assert sample["kind"] == ACTION


async def test_an_access_tag_reaches_the_index(store):
    """The shortlist is filtered server-side, like the knowledge base."""
    chunks = action_chunks(_specs())
    by_name = {c.source: c for c in chunks}
    assert by_name["secret_action"].department == "accounting"
    assert by_name["delete_project"].department is None


# -- what gets indexed ------------------------------------------------------


def test_the_indexed_text_is_wider_than_the_description():
    """«снеси проект» has to find a command whose description says «Удалить»."""
    text = action_chunks(_specs())[0].text
    assert "Удалить проект" in text
    assert "снести" in text and "убрать" in text


def test_a_parameters_description_is_searchable_too():
    text = {c.source: c.text for c in action_chunks(_specs())}["create_post"]
    assert "в каком проекте" in text


def test_the_fingerprint_ignores_order_and_notices_content():
    a = _specs()
    b = {k: a[k] for k in reversed(list(a))}
    assert fingerprint(a) == fingerprint(b)
    changed = _specs()
    changed["delete_project"].keywords.append("грохнуть")
    assert fingerprint(changed) != fingerprint(a)


# -- staleness --------------------------------------------------------------


class _RecordingStore:
    """Stands in for Qdrant: remembers whether a reindex was asked for."""

    def __init__(self, stored: dict | None) -> None:
        self.stored = stored
        self.deleted: list[str] = []
        self.upserted = 0

    async def payload_sample(self, kind):
        return self.stored

    async def delete_kind(self, kind):
        self.deleted.append(kind)

    async def upsert(self, chunks, vectors):
        self.upserted = len(chunks)


@pytest.fixture
def fake_backends(monkeypatch):
    """Swap Qdrant + the embedder out of the index module's lazy imports."""
    import onbo.rag.embeddings as embeddings
    import onbo.rag.qdrant_store as qdrant_store

    holder = {}

    def _store(settings):
        return holder["store"]

    class _Embedder:
        def __init__(self, settings):
            pass

        def encode(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(qdrant_store, "QdrantStore", _store)
    monkeypatch.setattr(embeddings, "Embedder", _Embedder)
    return holder


async def test_a_changed_actions_yaml_is_reindexed_on_start(fake_backends):
    specs = _specs()
    fake_backends["store"] = _RecordingStore({"fingerprint": "stale-one"})
    assert await reindex_if_stale(Settings(), specs) == len(specs)
    assert fake_backends["store"].deleted == [ACTION]


async def test_an_unchanged_actions_yaml_is_left_alone(fake_backends):
    specs = _specs()
    fake_backends["store"] = _RecordingStore({"fingerprint": fingerprint(specs)})
    assert await reindex_if_stale(Settings(), specs) == 0
    assert fake_backends["store"].deleted == []


async def test_an_empty_index_is_built(fake_backends):
    fake_backends["store"] = _RecordingStore(None)
    assert await reindex_if_stale(Settings(), _specs()) == 3


async def test_a_reindex_deletes_before_it_writes(fake_backends):
    """Otherwise a command dropped from actions.yaml keeps answering forever."""
    fake_backends["store"] = _RecordingStore(None)
    await reindex_actions(Settings(), _specs())
    assert fake_backends["store"].deleted == [ACTION]
    assert fake_backends["store"].upserted == 3


# -- the classifier's shortlist ---------------------------------------------


class _Retriever:
    def __init__(self, names=None, error=None):
        self.names = names or []
        self.error = error
        self.calls = 0

    async def search_actions(self, query, profile, limit=12):
        self.calls += 1
        if self.error:
            raise self.error
        return self.names


def _classifier(retriever=None, size=2, specs=None):
    return Classifier(
        llm=None,
        actions=specs or _specs(6),
        retriever=retriever,
        shortlist_size=size,
    )


async def test_only_the_found_commands_reach_the_prompt(profile):
    retriever = _Retriever(["delete_project"])
    short = await _classifier(retriever)._shortlist("снеси проект «телефон»", profile)
    assert set(short) == {"delete_project"}
    assert retriever.calls == 1


async def test_a_small_catalog_is_sent_whole(profile):
    """Searching six things to show six things buys nothing but a round trip."""
    retriever = _Retriever(["delete_project"])
    short = await _classifier(retriever, size=50)._shortlist("что угодно", profile)
    assert retriever.calls == 0
    assert len(short) == 5   # 6 specs minus the accounting-only one


async def test_qdrant_being_down_prints_the_whole_catalog(profile):
    """A long prompt is expensive; a silent «no command matches» is broken."""
    short = await _classifier(_Retriever(error=RuntimeError("connection refused")))._shortlist(
        "снеси проект", profile
    )
    assert "delete_project" in short and "create_post" in short


async def test_an_empty_index_prints_the_whole_catalog(profile):
    short = await _classifier(_Retriever([]))._shortlist("снеси проект", profile)
    assert "delete_project" in short and "create_post" in short


async def test_the_shortlist_never_offers_someone_elses_command(profile):
    """secret_action belongs to accounting; the index should not return it, and
    if it ever does, the shortlist still drops it."""
    short = await _classifier(_Retriever(["delete_project", "secret_action"]))._shortlist(
        "удали", profile
    )
    assert "secret_action" not in short


async def test_keyword_matches_are_added_not_replaced(profile):
    """The cheap match and the vector match disagree often enough to keep both."""
    short = await _classifier(_Retriever(["filler_0"]))._shortlist("снести проект", profile)
    assert set(short) == {"filler_0", "delete_project"}


async def test_the_parked_action_stays_in_the_list(profile):
    """«ещё раз» resembles no command at all — but it is about the parked one."""
    short = await _classifier(_Retriever(["filler_0"]))._shortlist(
        "ещё раз", profile, parked="create_post"
    )
    assert "create_post" in short


async def test_actions_turned_off_costs_no_search(profile):
    classifier = Classifier(llm=None, actions=_specs(6), actions_enabled=False,
                            retriever=_Retriever(["delete_project"]), shortlist_size=2)
    assert await classifier._shortlist("удали проект", profile) == {}


async def test_the_prompt_admits_the_shortlist_may_be_wrong(profile, monkeypatch):
    """Given ten lines to choose from, a model picks the least bad one unless
    told that none of them is allowed."""
    seen = {}

    class _LLM:
        async def structured(self, messages, model):
            seen["prompt"] = messages[0]["content"]
            raise RuntimeError("stop here")

    classifier = _classifier(_Retriever(["delete_project"]))
    classifier._llm = _LLM()
    await classifier.classify(Envelope(user_id="u1", channel="web", text="снеси проект"), profile)
    assert "do not pick one" in seen["prompt"]
    assert "secret_action" not in seen["prompt"]
