"""Combine per-action results into a single user-facing response."""
from __future__ import annotations

from .schemas import ActionResult, Response, ResultStatus


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
    failed = [r for r in results if r.status == ResultStatus.failed]

    for r in answers:
        lines.append(r.message)

    if confirms:
        lines.append("Нужно подтверждение:")
        lines += [f"• {r.confirm_prompt}" for r in confirms]

    if links:
        lines.append("Откройте страницу (чувствительные данные меняются там):")
        lines += [f"• {r.message} {r.link_url or ''}".rstrip() for r in links]

    if inputs:
        lines.append("Не хватает данных:")
        lines += [f"• {r.message}" for r in inputs]

    if failed:
        lines.append("Не смог выполнить:")
        lines += [f"• {r.message}" for r in failed]

    return Response(text="\n".join(lines), results=results)
