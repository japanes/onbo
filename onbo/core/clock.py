"""What "now" is for the person asking, and how it is told to the model.

A message like «создай пост на 25 июля на 11:15» carries no year, and «завтра»
carries no date at all. The model can only turn those into a real timestamp if
it is told what today is — and *today* is the asker's today, not the server's:
at 23:30 in Kyiv a UTC server is still on the previous date.

So every channel may put the caller's own local time into ``Envelope.ts``
(the web widget always does, ISO-8601 with its offset: ``2026-07-23T14:07+03:00``)
and this module turns it into one line of prompt. When the field is absent —
Telegram, a backend proxying the request, an old widget — the server's own UTC
clock is used instead, which is right to the hour and at worst a day off at the
edges.

The value is a hint to the model, never a permission: the worst a forged ``ts``
can do is make the sender schedule their own post on the wrong day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Weekday names are spelled out rather than taken from strftime("%A"), which
# follows the process locale and would quietly switch languages on us.
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

# How far the caller's clock may sit from the server's before we stop believing
# it. A day covers every real timezone (UTC-12..UTC+14) plus a slow phone, and
# still rejects the obvious nonsense — a browser set to 1970 or to next year.
MAX_SKEW = timedelta(hours=24)


def resolve_now(ts: str | None) -> datetime:
    """The caller's local time, or the server's UTC when it cannot be trusted.

    ``ts`` is ISO-8601, ideally with an offset. A value without one is taken as
    wall-clock time as the caller sees it — which is exactly what dates in a
    sentence are measured against.
    """
    server_now = datetime.now(timezone.utc)
    if not ts:
        return server_now
    try:
        parsed = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return server_now
    # Compare on the same footing: a naive value is the caller's wall clock, so
    # read it as UTC for the skew check only — never for what we show.
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if abs(moment - server_now) > MAX_SKEW:
        return server_now
    return parsed


def now_line(ts: str | None) -> str:
    """One line for a prompt: what time it is for this person, and how to use it.

    Kept as a sentence rather than a bare timestamp because the model has to be
    told the convention too — "25 июля" with no year means the next 25 July, not
    the one that has passed.
    """
    now = resolve_now(ts)
    stamp = now.isoformat(timespec="minutes")
    return (
        f"Current date and time for this user: {stamp} ({_WEEKDAYS[now.weekday()]}).\n"
        "Resolve every date and time in the message against it: a date given "
        "without a year means the nearest one still ahead, and «завтра»/«tomorrow», "
        "«в пятницу»/«on Friday» and the like are counted from this moment. "
        "Write resolved values in the same shape as the parameter's description asks for."
    )
