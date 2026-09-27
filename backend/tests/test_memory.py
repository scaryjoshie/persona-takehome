"""Memory: facts the agent remembers, what lookups showed it, and the rolling summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent.agent import agent
from app.agent.context import to_model_messages, turns
from app.agent.deps import AgentEnv
from app.database import SessionFactory
from app.events.event import Event
from app.events.payload import Channel, Origin, Payload
from app.memory import summarize
from app.memory.events import Forgot, Remembered
from app.memory.service import Summary
from app.memory.summarize import HIGH_TOKENS, KEEP_TOKENS, cut, summarize_if_due, tokens
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.text.reply import Replier
from app.voice.call_state import CallEvent, CallTransition
from app.voice.events import Speaker, VoiceUtterance
from tests.conftest import PHONE, CapturingMessenger, settle
from tests.test_google import FakeGoogle, connected, reply


async def text(pipeline: Pipeline, words: str) -> None:
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text=words), route=False)


async def remember(pipeline: Pipeline, fact: str) -> Event | None:
    return await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, Remembered(fact=fact))


# ---- facts --------------------------------------------------------------------------


async def test_facts_are_remembered_once_and_forgotten_by_number(pipeline: Pipeline) -> None:
    first = await remember(pipeline, "Dave is their landlord")
    assert first is not None and isinstance(first.payload, Remembered)
    assert await remember(pipeline, "Dave is their landlord") is None  # already remembered
    await remember(pipeline, "no texts before 9am")
    fact_id = first.payload.fact_id
    assert fact_id is not None

    gone = await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, Forgot(fact_id=fact_id))
    assert gone is not None and gone.payload == Forgot(
        fact_id=fact_id, fact="Dave is their landlord"
    )
    again = Forgot(fact_id=fact_id)
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, again) is None
    assert [f.text for f in (await pipeline.memory(PHONE)).facts] == ["no texts before 9am"]


async def test_the_agent_remembers_and_sees_what_it_remembered(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    seen: list[str] = []
    steps = iter(
        [
            [ToolCallPart("remember", {"fact": "their sister Ana is visiting in October"})],
            [ToolCallPart("final_result", {"bubbles": ["nice"]})],
        ]
    )

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        return ModelResponse(parts=next(steps))

    await text(pipeline, "my sister ana is visiting in october")
    model = FunctionModel(fn)
    with agent.override(model=model):
        await Replier(AgentEnv(pipeline, messenger, model, "http://x")).reply(PHONE, 1)
    await settle(pipeline)
    fact = (await pipeline.memory(PHONE)).facts[0]
    assert fact.text == "their sister Ana is visiting in October"
    assert "## What you remember about them" not in seen[0]
    assert "## What you remember about them" in seen[1]
    assert f"- [{fact.id}] their sister Ana is visiting in October" in seen[1]


# ---- lookups --------------------------------------------------------------------------


async def test_what_a_search_showed_stays_in_the_conversation(
    db: SessionFactory, pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    google = await connected(db, pipeline, FakeGoogle())
    await reply(pipeline, messenger, google, [ToolCallPart("search_email", {"query": "heater"})])
    notes = [t.text for t in turns(await pipeline.history(PHONE))]
    assert any("[m1] Maria: heater (next week)" in n for n in notes)


# ---- the rolling summary ---------------------------------------------------------------

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def at(seq: int, payload: Payload) -> Event:
    return Event(
        seq=seq,
        ts=T0 + timedelta(minutes=seq),
        origin=Origin.USER,
        channel=Channel.TEXT,
        payload=payload,
    )


def chatter(n: int, *, start: int = 1) -> list[Event]:
    """n texts of about 100 tokens each."""
    return [at(start + i, UserMessage(text="x" * 400)) for i in range(n)]


def test_nothing_is_summarized_below_the_high_mark() -> None:
    events = chatter(HIGH_TOKENS // 100 - 1)
    assert cut(events) == 0


def test_past_the_high_mark_the_latest_stay_word_for_word() -> None:
    events = chatter(HIGH_TOKENS // 100 + 5)
    n = cut(events)
    kept = sum(tokens(e) for e in events[n:])
    assert n > 0 and KEEP_TOKENS <= kept < KEEP_TOKENS + 100
    assert cut(events[n:]) == 0  # the buffer: no second summary until it fills again


def test_the_cut_never_falls_inside_a_call() -> None:
    before = chatter(40)
    connected = at(41, CallEvent(transition=CallTransition.CONNECTED))
    lines = [
        at(42 + i, VoiceUtterance(speaker=Speaker.USER, text="y" * 400, turn_id=f"user-{i}"))
        for i in range(HIGH_TOKENS // 100)
    ]
    events = [*before, connected, *lines]
    assert cut(events) == 40  # the call is still going at the cut: it waits, whole
    ended = at(500, CallEvent(transition=CallTransition.ENDED))
    after = chatter(KEEP_TOKENS // 100 + 1, start=501)
    assert cut([*before, connected, *lines, ended, *after]) > 41  # over: it can go


async def test_a_summary_replaces_what_it_covers(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    for i in range(HIGH_TOKENS // 100 + 10):
        await text(pipeline, f"message {i} " + "x" * 400)
    notes = "They asked about their heater; you offered to chase Maria."
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(notes)]))
    assert await summarize_if_due(pipeline, model, PHONE)
    assert not await summarize_if_due(pipeline, model, PHONE)  # the buffer is refilling

    memory, events = await pipeline.conversation(PHONE)
    assert memory.summary is not None and memory.summary.text == notes
    assert events[0].seq == memory.summary.through_seq + 1
    assert sum(tokens(e) for e in events) >= KEEP_TOKENS

    seen: list[tuple[str, int]] = []

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append((info.instructions or "", len(messages)))
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["ok"]})])

    reply_model = FunctionModel(fn)
    with agent.override(model=reply_model):
        await Replier(AgentEnv(pipeline, messenger, reply_model, "http://x")).reply(PHONE, 1)
    instructions, n = seen[0]
    assert "## Earlier with them" in instructions and notes in instructions
    assert n == len(to_model_messages(events))  # only what the summary doesn't cover


async def test_the_next_summary_folds_in_the_last(pipeline: Pipeline) -> None:
    await pipeline.save_summary(PHONE, Summary(through_seq=0, text="old notes"))
    for i in range(HIGH_TOKENS // 100 + 10):
        await text(pipeline, f"message {i} " + "x" * 400)
    prompts: list[str] = []

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        part = messages[-1].parts[-1]
        assert isinstance(part, UserPromptPart) and isinstance(part.content, str)
        prompts.append(part.content)
        return ModelResponse(parts=[TextPart("new notes")])

    assert await summarize_if_due(pipeline, FunctionModel(fn), PHONE)
    assert "old notes" in prompts[0] and "message 0" in prompts[0]
    assert summarize._running == set()  # pyright: ignore[reportPrivateUsage]


async def test_reset_forgets_memory(pipeline: Pipeline) -> None:
    await remember(pipeline, "likes mornings")
    await pipeline.save_summary(PHONE, Summary(through_seq=1, text="notes"))
    await pipeline.reset(PHONE)
    memory = await pipeline.memory(PHONE)
    assert memory.facts == () and memory.summary is None
