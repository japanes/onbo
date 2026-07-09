"""Embeddings (bge-m3 / e5) — used both at indexing and at query time.

Lazily loads sentence-transformers so importing the package stays cheap and
does not require the ``rag`` extra until embeddings are actually computed.
"""
from __future__ import annotations

from ..config import Settings

_model = None  # cached SentenceTransformer instance


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _load(self):
        global _model
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(self._settings.embedding.model)
        return _model

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        return model.encode(texts, normalize_embeddings=True).tolist()

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]
