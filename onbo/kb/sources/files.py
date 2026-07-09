"""File source: read Markdown / txt (and PDF / docx when the extra is present)."""
from __future__ import annotations

import os

from .base import RawDoc, Source

_TEXT_EXT = {".md", ".markdown", ".txt", ".rst"}


class FileSource(Source):
    def __init__(self, path: str) -> None:
        self._path = path

    def _iter_files(self):
        if os.path.isfile(self._path):
            yield self._path
            return
        for root, _dirs, names in os.walk(self._path):
            for name in names:
                yield os.path.join(root, name)

    def fetch(self) -> list[RawDoc]:
        docs: list[RawDoc] = []
        for file_path in self._iter_files():
            ext = os.path.splitext(file_path)[1].lower()
            if ext in _TEXT_EXT:
                with open(file_path, encoding="utf-8", errors="ignore") as handle:
                    body = handle.read()
            elif ext == ".pdf":
                body = _read_pdf(file_path)
            elif ext in {".docx", ".doc"}:
                body = _read_docx(file_path)
            else:
                continue  # skip unsupported file types
            if body.strip():
                docs.append(RawDoc(source=file_path, title=os.path.basename(file_path), body=body))
        return docs


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - optional dependency
        return ""
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _read_docx(path: str) -> str:
    try:
        import docx
    except ImportError:  # pragma: no cover - optional dependency
        return ""
    document = docx.Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)
