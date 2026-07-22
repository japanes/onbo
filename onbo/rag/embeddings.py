"""Embeddings — used both at indexing and at query time.

Two interchangeable backends, selected by ``embeddings.provider``:

* ``local`` — ``fastembed`` (ONNX runtime, no torch), what the ``rag`` extra
  installs. No API key, no vendor, nothing leaves the machine.
* ``api`` — a hosted model through LiteLLM: OpenAI (``text-embedding-3-large``),
  Gemini (``gemini/gemini-embedding-001``), Voyage (``voyage/voyage-3-large``),
  Cohere, Mistral, Bedrock, a self-hosted OpenAI-compatible server. Anthropic
  ships no embedding model of its own and points at Voyage AI, so Voyage is the
  "Anthropic" answer here.

``auto`` (the default) infers the backend from the model string. Either way the
model is loaded/resolved lazily, so importing the package stays cheap.

Changing the model — or the backend — changes the vector size, which means the
Qdrant collection must be rebuilt: ``onbo kb reindex``.
"""
from __future__ import annotations

from ..config import Settings

_model = None  # cached (model_name, TextEmbedding) pair for the local backend

# LiteLLM provider prefixes that mean "call a hosted embedding endpoint".
# fastembed model names are HuggingFace repo ids ("BAAI/bge-m3"), so a name with
# no slash at all is a vendor model too ("text-embedding-3-small").
_API_PREFIXES = {
    "azure",
    "azure_ai",
    "bedrock",
    "cohere",
    "databricks",
    "deepinfra",
    "fireworks_ai",
    "gemini",
    "jina_ai",
    "mistral",
    "nvidia_nim",
    "ollama",
    "openai",
    "together_ai",
    "vertex_ai",
    "voyage",
    "watsonx",
}
# Hosted endpoints limit how many inputs one request may carry.
_API_BATCH = 96


class EmbeddingsUnavailable(RuntimeError):
    """Raised when the backend the settings ask for cannot be used."""


def uses_api(settings: Settings) -> bool:
    """Whether embeddings go to a hosted vendor instead of running locally."""
    cfg = settings.embeddings
    if cfg.provider != "auto":
        return cfg.provider == "api"
    prefix, _, rest = cfg.model.partition("/")
    return not rest or prefix in _API_PREFIXES


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _load(self):
        global _model
        name = self._settings.embeddings.model
        if _model is None or _model[0] != name:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - depends on extras
                raise EmbeddingsUnavailable(
                    "fastembed is not installed (pip install 'onbo[rag]')"
                ) from exc

            _model = (name, TextEmbedding(model_name=name))
        return _model[1]

    def _encode_api(self, texts: list[str]) -> list[list[float]]:
        cfg = self._settings.embeddings
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise EmbeddingsUnavailable(
                f"embeddings model {cfg.model!r} needs litellm "
                "(pip install 'onbo[llm]')"
            ) from exc

        vectors: list[list[float]] = []
        for start in range(0, len(texts), _API_BATCH):
            response = litellm.embedding(
                model=cfg.model,
                input=texts[start : start + _API_BATCH],
                api_key=cfg.api_key,
                api_base=cfg.api_base,
            )
            # LiteLLM normalises every vendor to OpenAI's response shape, and
            # `data` comes back in request order.
            vectors.extend(item["embedding"] for item in response["data"])
        return vectors

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # No silent fallback between backends: their vector sizes differ, and
        # mixing them would quietly corrupt the index instead of failing.
        if uses_api(self._settings):
            return self._encode_api(texts)
        # fastembed yields one numpy array per input, in order.
        return [vector.tolist() for vector in self._load().embed(texts)]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]
