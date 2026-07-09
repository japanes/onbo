"""Website source: crawl a URL and extract readable text."""
from __future__ import annotations

from .base import RawDoc, Source


class WebsiteSource(Source):
    def __init__(self, url: str, max_pages: int = 20) -> None:
        self._url = url
        self._max_pages = max_pages

    def fetch(self) -> list[RawDoc]:
        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover - optional dependency
            return []

        # Skeleton: fetch the single entry URL. A fuller crawler would follow
        # in-domain links up to max_pages.
        response = httpx.get(self._url, follow_redirects=True, timeout=30)
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string if soup.title else self._url
        body = soup.get_text(separator="\n", strip=True)
        return [RawDoc(source=self._url, title=title, body=body)]
