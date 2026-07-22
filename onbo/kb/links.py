"""Deep links attached to a Q&A pair, and how they are written out.

Kept as structured data (``{title, url}``) rather than glued into the answer
text: a widget can then render buttons, Telegram a list, and a plain client the
fallback block below — none of them has to fish URLs out of a sentence.
"""
from __future__ import annotations

# Heading of the fallback block appended to the answer text. Channels that render
# `links` themselves can strip everything from this line down.
LINKS_HEADING = "Ссылки:"


def normalize_links(items: object) -> list[dict]:
    """Accept the shapes a YAML file or an API caller might use, return one shape.

    Each item may be a bare URL string, or a mapping with ``url`` plus an optional
    ``title`` (falling back to the URL, so a link is never rendered as a blank
    label). Anything without a URL is dropped rather than stored half-formed.
    """
    if not items:
        return []
    if isinstance(items, (str, dict)):
        items = [items]
    links: list[dict] = []
    for item in items:
        if isinstance(item, str):
            url = item.strip()
            title = url
        elif isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip() or url
        else:
            continue
        if url:
            links.append({"title": title, "url": url})
    return links


def render_links(links: list[dict]) -> str:
    """The plain-text block appended to an answer, empty when there is nothing to add."""
    if not links:
        return ""
    lines = [f"- {link['title']}: {link['url']}" for link in links]
    return f"{LINKS_HEADING}\n" + "\n".join(lines)
