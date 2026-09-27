"""The text medium, driven through the real pipeline with a fake clock and timers."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta

import httpx
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.events.decision import Decision
from app.events.payload import Channel, Origin
from app.jev import Jev
from app.llm import Models
from app.main import App
from app.pipeline import Pipeline
from app.text.events import ReplyStarted, Typing, UserMessage
from app.text.reply import Replier
from app.text.responder import TextResponder
from app.text.timing import delay, waiting
from app.users.user import Medium
from app.voice.call_state import CallEvent, CallTransition
from tests.conftest import (
    PHONE,
    CapturingMessenger,
    FakeClock,
    FakeTimers,
    ev,
    no_sleep,
    settle,
    user_text,
)


async def say(pipeline: Pipeline, text: str) -> None:
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, UserMessage(text=text))


async def type_(pipeline: Pipeline, active: bool) -> None:
    await pipeline.submit(PHONE, Origin.USER, Channel.TEXT, Typing(active=active))


async def fire(timers: FakeTimers, pipeline: Pipeline) -> None:
    timers.fire_next()
    await settle(pipeline)


async def decisions(pipeline: Pipeline) -> list[str]:
    return [
        e.payload.verb for e in await pipeline.history(PHONE) if isinstance(e.payload, Decision)
    ]


# ---- timing: pure ------------------------------------------------------------------------


def test_waiting_is_everything_since_the_last_reply() -> None:
    log = [
        user_text("a").model_copy(update={"seq": 1}),
        ev(ReplyStarted(through_seq=1)).model_copy(update={"seq": 2}),
        user_text("b").model_copy(update={"seq": 3}),
        user_text("c").model_copy(update={"seq": 4}),
    ]
    assert [e.seq for e in waiting(log)] == [3, 4]
    assert waiting(log[:2]) == []


def test_delay_rules() -> None:
    msg = user_text("hi")
    now = msg.ts
    assert delay([msg], None, now) == 1.5
    assert delay([msg], now, now) == 5.0  # typing
    assert delay([msg], None, now + timedelta(seconds=1)) == 0.5
    later = msg.model_copy(update={"ts": now + timedelta(seconds=7)})
    assert delay([msg, later], now, now + timedelta(seconds=7.5)) == 0.5  # 8 s cap from first
    assert delay([msg] * 6, None, now) == 0.0  # six waiting
    outcome = ev(CallEvent(transition=CallTransition.ENDED), Origin.CALL, Channel.SYSTEM)
    assert delay([msg, outcome], None, now) == 0.0  # answer outcomes now


# ---- the medium, end to end ----------------------------------------------------------------


async def test_a_message_waits_then_replies_once(
    pipeline: Pipeline, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    await say(pipeline, "hey")
    assert timers.pending == [1.5] and messenger.sent == []
    await fire(timers, pipeline)
    assert messenger.sent == ["hi"]
    kinds = [e.kind for e in await pipeline.history(PHONE)]
    assert kinds == ["user_message", "decision", "reply_started", "decision", "agent_message"]
    assert await decisions(pipeline) == ["schedule", "reply"]


async def test_a_burst_gets_one_reply(
    pipeline: Pipeline, timers: FakeTimers, clock: FakeClock, messenger: CapturingMessenger
) -> None:
    await say(pipeline, "one")
    clock.advance(0.5)
    await say(pipeline, "two")
    clock.advance(0.5)
    await say(pipeline, "three")
    while timers.pending:
        await fire(timers, pipeline)
    assert messenger.sent == ["hi"]
    assert (await decisions(pipeline)).count("reply") == 1


async def test_typing_holds_the_reply(
    pipeline: Pipeline, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    await say(pipeline, "i think")
    await type_(pipeline, True)
    await fire(timers, pipeline)  # the 1.5 s check finds the user typing
    assert messenger.sent == [] and "wait" in await decisions(pipeline)
    await type_(pipeline, False)
    while timers.pending:
        await fire(timers, pipeline)
    assert messenger.sent == ["hi"]


async def test_a_hang_up_is_answered_at_once(
    pipeline: Pipeline, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    for transition in (CallTransition.CONNECTING, CallTransition.CONNECTED):
        await pipeline.submit(PHONE, Origin.CALL, Channel.SYSTEM, CallEvent(transition=transition))
    ended = CallEvent(transition=CallTransition.ENDED, reason="user_hangup")
    await pipeline.submit(PHONE, Origin.CALL, Channel.SYSTEM, ended)
    assert timers.pending == [0.0]
    await fire(timers, pipeline)
    assert messenger.sent == ["hi"]


async def test_a_newer_message_supersedes_a_reply_in_flight(
    app: App,
    pipeline: Pipeline,
    timers: FakeTimers,
    clock: FakeClock,
    messenger: CapturingMessenger,
) -> None:
    release = asyncio.Event()
    replies: list[int] = []

    async def slow_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        replies.append(len(replies))
        if len(replies) == 1:
            await release.wait()  # the first reply is still thinking...
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["hi"]})])

    env = dataclasses.replace(app.env, models=Models.same(FunctionModel(slow_model)))
    pipeline.responders[Medium.TEXT] = TextResponder(Replier(env, sleep=no_sleep))
    await say(pipeline, "book a dentist")
    timers.fire_next()
    for _ in range(200):  # let the check run and the first reply start
        if replies:
            break
        await asyncio.sleep(0.01)
    assert len(replies) == 1
    await say(pipeline, "actually nvm")  # ...when a newer message lands
    release.set()
    await settle(pipeline)
    while timers.pending:
        await fire(timers, pipeline)
    assert len(replies) == 2  # both replies were generated
    assert messenger.sent == ["hi"]  # but only the second one was sent


async def test_a_second_check_after_the_reply_is_dropped(
    pipeline: Pipeline, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    await say(pipeline, "hey")
    await type_(pipeline, False)  # schedules a second check
    while timers.pending:
        await fire(timers, pipeline)
    assert messenger.sent == ["hi"]


def jev_says_finished(probability: float) -> Jev:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"q": {"noul": probability}}})

    return Jev(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))


async def test_a_first_text_that_is_just_hi_gets_the_opener_as_written(
    app: App, pipeline: Pipeline, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    from app.agent.prompts import OPENER

    env = dataclasses.replace(app.env, jev=jev_says_finished(0.9))  # "yes, it's only a hi"
    pipeline.responders[Medium.TEXT] = TextResponder(Replier(env, sleep=no_sleep))
    await say(pipeline, "hey")
    await fire(timers, pipeline)
    assert len(messenger.sent) == 2 and messenger.sent[0].startswith("hey! 👋")
    assert " / ".join(messenger.sent) == OPENER["example"].removeprefix("- ")


async def test_a_first_text_that_says_more_gets_a_written_reply(
    app: App, pipeline: Pipeline, timers: FakeTimers, messenger: CapturingMessenger
) -> None:
    env = dataclasses.replace(app.env, jev=jev_says_finished(0.1))  # "no, there's more in it"
    pipeline.responders[Medium.TEXT] = TextResponder(Replier(env, sleep=no_sleep))
    await say(pipeline, "hey, i'm Sam, can you sort out my inbox?")
    await fire(timers, pipeline)
    assert messenger.sent == ["hi"]  # the model's own (reply_hi)


async def test_a_clearly_finished_text_is_answered_early(
    app: App, pipeline: Pipeline, timers: FakeTimers
) -> None:
    replier = Replier(app.env, sleep=no_sleep)
    pipeline.responders[Medium.TEXT] = TextResponder(replier, jev=jev_says_finished(0.9))
    await say(pipeline, "call me Sam")
    assert timers.pending == [0.4]
    await fire(timers, pipeline)
    assert "reply" in await decisions(pipeline)


async def test_an_unclear_text_waits_out_the_quiet_window(
    app: App, pipeline: Pipeline, timers: FakeTimers
) -> None:
    replier = Replier(app.env, sleep=no_sleep)
    pipeline.responders[Medium.TEXT] = TextResponder(replier, jev=jev_says_finished(0.5))
    await say(pipeline, "hey")
    await fire(timers, pipeline)
    assert "reply" not in await decisions(pipeline) and timers.pending == [1.1]
    await fire(timers, pipeline)
    assert "reply" in await decisions(pipeline)
