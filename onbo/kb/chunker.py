"""Split documents into overlapping chunks for embedding."""
from __future__ import annotations


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Naive character-window chunker with overlap.

    A fuller implementation would split on sentence / heading boundaries; this
    keeps the skeleton dependency-free and deterministic.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(text):
        chunks.append(text[start : start + size])
        start += step
    return chunks
