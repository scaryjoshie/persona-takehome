"""Their timezone: a guess until their calendar or they say otherwise, and every time a model
sees is in it."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.agent import Bubbles, agent
from app.agent.context import their_time, turns
from app.agent.deps import AgentEnv
from app.agent.events import TimezoneLearned
from app.agent.slots import DEFAULT_TZ, Slots, TzSource
from app.events.event import Event
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.users.user import Medium
from app.voice.call_state import CallEvent, CallTransition
from tests.conftest import PHONE, CapturingMessenger


async def learn(pipeline: Pipeline, tz: str, source: TzSource) -> Event | None:
    learned = TimezoneLearned(tz=tz, source=source)
    return await pipeline.submit(PHONE, Origin.GOOGLE, Channel.SYSTEM, learned)


def test_until_we_know_it_is_an_eastern_guess() -> None:
    now = datetime(2026, 9, 27, 21, 11, tzinfo=UTC)
    assert Slots().zone() == ZoneInfo(DEFAULT_TZ)
    assert their_time(Slots(), now) == "It's Sunday September 27, 5:11 PM."  # just the time
    browser = Slots(timezone="America/Los_Angeles")  # from before: no source, so it doesn't count
    assert browser.zone() == ZoneInfo(DEFAULT_TZ)
    said = Slots(timezone="America/Denver", timezone_source=TzSource.SAID)
    assert their_time(said, now) == "It's Sunday September 27, 3:11 PM."


async def test_what_they_said_beats_their_calendar(pipeline: Pipeline) -> None:
    assert await learn(pipeline, "America/New_York", TzSource.CALENDAR) is not None
    assert await learn(pipeline, "America/Denver", TzSource.SAID) is not None
    assert await learn(pipeline, "America/Chicago", TzSource.CALENDAR) is None  # they said
    slots = (await pipeline.user(PHONE)).slots
    assert (slots.timezone, slots.timezone_source) == ("America/Denver", TzSource.SAID)
    assert await learn(pipeline, "Europe/London", TzSource.SAID) is not None  # they moved on


async def test_set_timezone_takes_only_real_zones(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    results: list[str] = []
    steps = iter(
        [
            [ToolCallPart("set_timezone", {"tz": "Mountain"})],
            [ToolCallPart("set_timezone", {"tz": "America/Denver"})],
            [ToolCallPart("final_result", {"bubbles": []})],
        ]
    )

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1].parts[-1]
        results.append(str(getattr(last, "content", "")))
        return ModelResponse(parts=next(steps))

    model = FunctionModel(fn)
    env = AgentEnv(pipeline, messenger, model, "")
    deps = env.deps(await pipeline.user(PHONE), Medium.TEXT)
    with agent.override(model=model):
        await agent.run("im in denver", deps=deps, output_type=Bubbles)
    assert "isn't a timezone name" in results[1] and "America/Denver" in results[2]
    assert (await pipeline.user(PHONE)).slots.zone() == ZoneInfo("America/Denver")


def at(ts: datetime, payload: CallEvent | UserMessage) -> Event:
    return Event(seq=0, ts=ts, origin=Origin.USER, channel=Channel.TEXT, payload=payload)


def test_times_are_theirs_and_days_are_marked_only_when_they_change() -> None:
    eastern = ZoneInfo(DEFAULT_TZ)
    connected = at(
        datetime(2026, 9, 27, 21, 11, tzinfo=UTC), CallEvent(transition=CallTransition.CONNECTED)
    )
    assert turns([connected], eastern)[0].text == "call connected 17:11"

    later = at(datetime(2026, 9, 29, 14, 0, tzinfo=UTC), UserMessage(text="hey again"))
    rendered = [t.text for t in turns([connected, later], eastern)]
    assert rendered == [
        "Sunday September 27\ncall connected 17:11\nTuesday September 29",
        "hey again",
    ]
