"""Combine per-action results into a single user-facing response."""
from __future__ import annotations

from ..kb.links import render_links, strip_link_block
from .schemas import ActionResult, Response, ResultStatus


def _links_block(results: list[ActionResult]) -> str:
    """Every link of the turn, once, as the plain-text block channels expect.

    Links travel structured (`ActionResult.links`) so a channel can draw them as
    buttons, and as one text block at the very end for channels that only print
    text. Gathering them here — instead of leaving each answer to glue its own
    copy in — means a client has exactly one block to strip, wherever the links
    came from: a knowledge-base answer, or an action too sensitive to run in chat.
    """
    seen: dict[str, str] = {}
    for result in results:
        for link in result.links:
            seen.setdefault(link.url, link.title)
    return render_links([{"title": title, "url": url} for url, title in seen.items()])


def aggregate(results: list[ActionResult]) -> Response:
    """Merge results of a multi-action request into one message.

    Everything the user asked for in a single turn is answered in a single
    reply: what was done, what needs confirmation, what needs a link, and what
    could not be handled — each in its own section.
    """
    if not results:
        return Response(text="Не удалось распознать запрос. Попробуйте переформулировать.", results=results)

    lines: list[str] = []

    answers = [r for r in results if r.status in (ResultStatus.answer, ResultStatus.done)]
    confirms = [r for r in results if r.status == ResultStatus.needs_confirm]
    links = [r for r in results if r.status == ResultStatus.link]
    inputs = [r for r in results if r.status == ResultStatus.needs_input]
    dry = [r for r in results if r.status == ResultStatus.dry_run]
    failed = [r for r in results if r.status == ResultStatus.failed]

    for r in answers:
        # Its own copy of the links goes; they come back once, at the end.
        lines.append(strip_link_block(r.message))

    if dry:
        lines.append("Демо-режим (реальный бэкенд продукта не подключён):")
        lines += [f"• {r.message}" for r in dry]

    if confirms:
        lines.append("Нужно подтверждение:")
        lines += [f"• {r.confirm_prompt}" for r in confirms]

    if links:
        lines.append("Откройте страницу (чувствительные данные меняются там):")
        lines += [
            f"• {r.message}" if r.links else f"• {r.message} {r.link_url or ''}".rstrip()
            for r in links
        ]

    if inputs:
        lines.append("Не хватает данных:")
        lines += [f"• {r.message}" for r in inputs]

    if failed:
        lines.append("Не смог выполнить:")
        lines += [f"• {r.message}" for r in failed]

    text = "\n".join(lines)
    block = _links_block(results)
    return Response(text=f"{text}\n\n{block}" if block else text, results=results)
