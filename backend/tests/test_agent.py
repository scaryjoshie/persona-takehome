from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent import prompts
from app.agent.agent import RING_SECONDS, Bubbles, agent
from app.agent.deps import AgentEnv, Deps
from app.agent.events import CallOptOut, SlotChanged
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.text.reply import Replier
from app.users.user import Medium, User
from app.voice.call_state import CallPhase
from tests.conftest import PHONE, CapturingMessenger, FakeTimers, settle


def scripted(*responses: list[ToolCallPart]) -> FunctionModel:
    calls = iter(responses)

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=list(next(calls)))

    return FunctionModel(fn)


async def run_text(
    pipeline: Pipeline, messenger: CapturingMessenger, model: FunctionModel, sleeps: list[float]
) -> None:
    async def sleep(s: float) -> None:
        sleeps.append(s)

    env = AgentEnv(pipeline=pipeline, messenger=messenger, model=model, app_base_url="http://x")
    replier = Replier(env, sleep=sleep)
    trigger = await pipeline.submit(
        PHONE, Origin.USER, Channel.TEXT, UserMessage(text="hi, I'm Sam"), route=False
    )
    assert trigger is not None
    with agent.override(model=model):
        await replier.reply(PHONE, trigger.seq)


async def test_handler_runs_tools_and_delivers_bubbles(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    model = scripted(
        [ToolCallPart("set_user_name", {"name": "Sam"})],
        [ToolCallPart("final_result", {"bubbles": ["hey Sam", "what should I call me?"]})],
    )
    sleeps: list[float] = []
    await run_text(pipeline, messenger, model, sleeps)
    assert messenger.sent == ["hey Sam", "what should I call me?"]
    assert (await pipeline.user(PHONE)).slots.user_name == "Sam"
    kinds = [e.kind for e in await pipeline.history(PHONE)]
    assert kinds == ["user_message", "slot_changed", "tool_call", "agent_message", "agent_message"]
    assert messenger.typing == [True, True, True, False] and sleeps[0] < sleeps[1]


async def test_zero_bubbles_is_a_hold(pipeline: Pipeline, messenger: CapturingMessenger) -> None:
    await run_text(
        pipeline, messenger, scripted([ToolCallPart("final_result", {"bubbles": []})]), []
    )
    assert messenger.sent == [] and messenger.typing == [True, False]  # dots while thinking
    assert [e.kind for e in await pipeline.history(PHONE)] == ["user_message"]


async def test_tools_are_filtered_by_medium(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    seen: dict[str, set[str]] = {}

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["names"] = {t.name for t in info.function_tools}
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["ok"]})])

    user = await pipeline.user(PHONE)
    for medium, present, absent in (
        (Medium.TEXT, "start_call", "end_call"),
        (Medium.VOICE, "end_call", "start_call"),
    ):
        deps = AgentEnv(pipeline, messenger, FunctionModel(fn), "").deps(user, medium)
        with agent.override(model=FunctionModel(fn)):
            await agent.run("x", deps=deps, output_type=Bubbles)
        assert present in seen["names"] and absent not in seen["names"]


async def test_instructions_include_state_and_text_tail(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    captured: dict[str, str] = {}

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["instructions"] = getattr(messages[0], "instructions", "") or ""
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": []})])

    await pipeline.submit(
        PHONE, Origin.TEXT_AGENT, Channel.TEXT, SlotChanged(slot="agent_name", new="Jarvis")
    )
    env = AgentEnv(pipeline, messenger, FunctionModel(fn), "")
    deps = env.deps(await pipeline.user(PHONE), Medium.TEXT)
    with agent.override(model=FunctionModel(fn)):
        await agent.run("x", deps=deps, output_type=Bubbles)
    text = captured["instructions"]
    assert "Your name is Jarvis." in text and "Still missing: user name, gmail." in text
    assert "zero to four" in text and prompts.PERSONA[:40] in text


async def test_naming_the_agent_sends_its_contact_card(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    model = scripted(
        [ToolCallPart("set_agent_name", {"name": "Mila"})],
        [ToolCallPart("final_result", {"bubbles": ["mila it is"], "react": "❤️"})],
    )
    await run_text(pipeline, messenger, model, [])
    kinds = [e.kind for e in await pipeline.history(PHONE)]
    assert "contact_card" in kinds and kinds[-1] == "agent_message"
    tapbacks = [e for e in await pipeline.history(PHONE) if e.kind == "reaction"]
    assert len(tapbacks) == 1 and tapbacks[0].payload.model_dump()["by"] == "agent"


async def tools_for(
    pipeline: Pipeline, messenger: CapturingMessenger, deps_of: Callable[[AgentEnv, User], Deps]
) -> set[str]:
    seen: set[str] = set()

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.update(t.name for t in info.function_tools)
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": []})])

    env = AgentEnv(pipeline, messenger, FunctionModel(fn), "")
    with agent.override(model=FunctionModel(fn)):
        await agent.run("ok", deps=deps_of(env, await pipeline.user(PHONE)), output_type=Bubbles)
    return seen


async def test_on_a_call_only_the_back_office_records_and_sends(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    voice = await tools_for(pipeline, messenger, lambda env, u: env.deps(u, Medium.VOICE))
    assert voice == {"end_call"}
    office = await tools_for(
        pipeline, messenger, lambda env, u: env.deps(u, Medium.VOICE, back_office=True)
    )
    assert {"set_user_name", "send_gmail_link", "send_text", "end_call"} <= office
    assert "start_call" not in office


async def test_after_a_no_the_agent_calls_only_when_asked(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, CallOptOut())
    seen: set[str] = set()

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.clear()
        seen.update(t.name for t in info.function_tools)
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": []})])

    deps = AgentEnv(pipeline, messenger, FunctionModel(fn), "").deps(
        await pipeline.user(PHONE), Medium.TEXT
    )
    with agent.override(model=FunctionModel(fn)):
        await agent.run("what can you do", deps=deps, output_type=Bubbles)
        assert "start_call" not in seen and "no_call" in seen
        await agent.run("ok actually call me", deps=deps, output_type=Bubbles)
        assert "start_call" in seen


async def test_an_unanswered_call_becomes_a_missed_call(
    pipeline: Pipeline, messenger: CapturingMessenger, timers: FakeTimers
) -> None:
    model = scripted(
        [ToolCallPart("start_call", {"reason": "setup"})],
        [ToolCallPart("final_result", {"bubbles": ["calling you now"]})],
    )
    await run_text(pipeline, messenger, model, [])
    assert (await pipeline.user(PHONE)).call.phase is CallPhase.RINGING
    assert RING_SECONDS in timers.pending
    while (await pipeline.user(PHONE)).call.phase is CallPhase.RINGING:
        timers.fire_next()
        await settle(pipeline)
    call = (await pipeline.user(PHONE)).call
    assert call.phase is CallPhase.NONE and call.reason == "no_answer"
    assert not (await pipeline.user(PHONE)).slots.no_calls  # missing a call isn't a no


async def test_a_reply_that_places_a_call_sends_no_bubbles(
    pipeline: Pipeline, messenger: CapturingMessenger
) -> None:
    model = scripted(
        [ToolCallPart("start_call", {"reason": "setup"})],
        [ToolCallPart("final_result", {"bubbles": ["what do you want to call me?"]})],
    )
    await run_text(pipeline, messenger, model, [])
    assert messenger.sent == []  # the call took over; the bubble is dropped
    kinds = [e.kind for e in await pipeline.history(PHONE)]
    assert "call" in kinds and "agent_message" not in kinds
