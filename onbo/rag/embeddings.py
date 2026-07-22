"""Embeddings — used both at indexing and at query time.

Backed by ``fastembed`` (ONNX runtime, no torch), which is what the ``rag``
extra installs. The model is loaded lazily and cached, so importing the package
stays cheap and requires the extra only when embeddings are actually computed.
"""
from __future__ import annotations

from ..config import Settings

_model = None  # cached (model_name, TextEmbedding) pair


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _load(self):
        global _model
        name = self._settings.embeddings.model
        if _model is None or _model[0] != name:
            from fastembed import TextEmbedding

            _model = (name, TextEmbedding(model_name=name))
        return _model[1]

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        # fastembed yields one numpy array per input, in order.
        return [vector.tolist() for vector in model.embed(texts)]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]
