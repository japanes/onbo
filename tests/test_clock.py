"""«Сегодня» приходит от того, кто спрашивает — и доезжает до промпта.

Без этого «создай пост на 25 июля на 11:15» не превратить в дату: в фразе нет
года, а у модели нет часов. Дата берётся с часов звонящего (браузер знает свой
пояс), а не сервера: в UTC уже завтра, когда в Киеве ещё вечер.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from onbo.channels.base import Channel
from onbo.core.classifier import Classifier
from onbo.core.clock import now_line, resolve_now
from onbo.core.schemas import ActionType, Classification, ClassifiedAction, Envelope
from onbo.handlers.actions.registry import ActionSpec, ParamSpec

SPECS = {
    "create_post": ActionSpec(
        name="create_post",
        description="Создать пост",
        params={
            "scheduled_date": ParamSpec(description="когда публиковать, вида 2026-07-25T11:15"),
        },
    )
}


def _in_an_hour(offset_hours: int = 3) -> str:
    """A plausible «сейчас» from a browser in UTC+offset, as it would send it."""
    zone = timezone(timedelta(hours=offset_hours))
    return datetime.now(zone).isoformat(timespec="seconds")


class _CapturingLLM:
    """Remembers the prompt, so a test can see what the model was actually told."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def structured(self, messages, schema):
        self.prompts.append(messages[0]["content"])
        if schema is Classification:
            return Classification(
                actions=[ClassifiedAction(type=ActionType.profile_action, action="create_post")]
            )
        raise RuntimeError("no LLM")   # the fill() call falls back to what it has


# -- the clock itself --------------------------------------------------------

def test_the_callers_own_time_is_used_offset_and_all():
    ts = "2026-07-23T23:40:00+03:00"
    now = resolve_now(ts)
    # 23:40 in +03 is 20:40 UTC — the day is still the 23rd for this person, and
    # that is the day «завтра» is counted from.
    assert now.day == 23
    assert now.hour == 23
    assert now.utcoffset() == timedelta(hours=3)


def test_no_time_sent_falls_back_to_the_server():
    """Telegram, a proxying backend, an old widget — all send nothing."""
    assert abs(resolve_now(None) - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_a_broken_or_absurd_clock_is_ignored():
    server_now = datetime.now(timezone.utc)
    for value in ("вчера", "", "2026-13-45T99:99", "1970-01-01T00:00:00+00:00"):
        assert abs(resolve_now(value) - server_now) < timedelta(seconds=5)


def test_the_line_says_the_date_the_weekday_and_the_convention():
    line = now_line("2026-07-23T14:07:00+03:00")
    assert "2026-07-23T14:07+03:00" in line
    assert "Thursday" in line
    assert "without a year" in line


# -- and where it is used ----------------------------------------------------

async def test_classification_is_told_what_day_it_is(profile):
    llm = _CapturingLLM()
    ts = "2026-07-23T14:07:00+03:00"
    await Classifier(llm, SPECS).classify(
        Envelope(user_id=profile.user_id, channel="web", text="создай пост на 25 июля", ts=ts),
        profile,
    )
    assert "2026-07-23T14:07+03:00" in llm.prompts[0]


async def test_the_follow_up_question_is_told_too(profile):
    """«На какую дату?» — «25 июля»: the answer needs the year just as much."""
    llm = _CapturingLLM()
    await Classifier(llm, SPECS).fill(
        SPECS["create_post"], ["scheduled_date"], "25 июля", "2026-07-23T14:07:00+03:00"
    )
    assert "2026-07-23T14:07+03:00" in llm.prompts[0]


def test_a_channel_passes_the_time_through_untouched():
    class _Web(Channel):
        name = "web"

        async def start(self) -> None:  # pragma: no cover - not used here
            ...

    env = _Web.build_envelope(_Web(None, None), "u1", "привет", "ru", _in_an_hour())
    assert env.ts is not None
    assert resolve_now(env.ts).utcoffset() == timedelta(hours=3)
