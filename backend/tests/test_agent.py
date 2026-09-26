from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent import prompts
from app.agent.agent import Bubbles, agent
from app.agent.deps import AgentEnv
from app.agent.events import SlotChanged
from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.text.reply import Replier
from app.users.user import Medium
from tests.conftest import PHONE, CapturingMessenger


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
    assert messenger.typing == [True, True, False] and sleeps[0] < sleeps[1]


async def test_zero_bubbles_is_a_hold(pipeline: Pipeline, messenger: CapturingMessenger) -> None:
    await run_text(
        pipeline, messenger, scripted([ToolCallPart("final_result", {"bubbles": []})]), []
    )
    assert messenger.sent == [] and messenger.typing == [False]
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
    assert "agent_name: Jarvis" in text and "still need: user_name, help_need, gmail" in text
    assert "zero to four" in text and prompts.PERSONA[:40] in text
