"""Embedding backend selection: local fastembed vs a hosted vendor via LiteLLM."""
from __future__ import annotations

import sys
import types

import pytest

from onbo.config import EmbeddingSettings, Settings
from onbo.rag.embeddings import Embedder, uses_api


def _settings(**embeddings) -> Settings:
    return Settings(embeddings=EmbeddingSettings(**embeddings))


@pytest.mark.parametrize(
    "model",
    ["text-embedding-3-large", "gemini/gemini-embedding-001", "voyage/voyage-3-large"],
)
def test_auto_detects_hosted_models(model):
    assert uses_api(_settings(model=model))


@pytest.mark.parametrize(
    "model",
    ["intfloat/multilingual-e5-large", "BAAI/bge-m3", "sentence-transformers/all-MiniLM-L6-v2"],
)
def test_auto_detects_fastembed_repo_ids(model):
    assert not uses_api(_settings(model=model))


def test_explicit_provider_overrides_the_guess():
    # A self-hosted server whose model name looks like a HuggingFace repo id.
    assert uses_api(_settings(model="Qwen/Qwen3-Embedding-0.6B", provider="api"))
    assert not uses_api(_settings(model="text-embedding-3-small", provider="local"))


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        EmbeddingSettings(provider="openai")


def test_empty_key_becomes_none():
    # `${EMBED_API_KEY:-}` expands to "" — LiteLLM must see None so it can fall
    # back to OPENAI_API_KEY & co from the environment.
    cfg = EmbeddingSettings(api_key="", api_base="")
    assert cfg.api_key is None and cfg.api_base is None


class _FakeLiteLLM(types.ModuleType):
    """Stand-in for the litellm module: records calls, returns fixed vectors."""

    def __init__(self) -> None:
        super().__init__("litellm")
        self.calls: list[dict] = []

    def embedding(self, **kwargs):
        self.calls.append(kwargs)
        return {"data": [{"embedding": [0.1, 0.2]} for _ in kwargs["input"]]}


@pytest.fixture
def fake_litellm(monkeypatch):
    module = _FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module


def test_hosted_encode_passes_model_and_key(fake_litellm):
    embedder = Embedder(_settings(model="text-embedding-3-small", api_key="sk-test"))
    assert embedder.encode(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    call = fake_litellm.calls[0]
    assert call["model"] == "text-embedding-3-small"
    assert call["api_key"] == "sk-test"
    assert call["api_base"] is None  # hosted vendor -> LiteLLM's own endpoint
    assert call["input"] == ["a", "b"]


def test_hosted_encode_batches_long_inputs(fake_litellm):
    embedder = Embedder(_settings(model="voyage/voyage-3-large"))
    vectors = embedder.encode([f"chunk {i}" for i in range(200)])
    assert len(vectors) == 200
    # Split into 96 + 96 + 8, in order, so vendor per-request limits are respected.
    assert [len(call["input"]) for call in fake_litellm.calls] == [96, 96, 8]


def test_empty_input_calls_nothing(fake_litellm):
    assert Embedder(_settings(model="text-embedding-3-small")).encode([]) == []
    assert fake_litellm.calls == []
