from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.actions import Actions
from app.agent import prompts
from app.agent.agent import Bubbles, agent
from app.agent.deps import Deps
from app.events.payload import Channel, Origin
from app.routing.types import Medium
from app.text.events import UserMessage
from app.text.reply import Reply
from app.text.responder import RunRequest
from tests.conftest import PHONE, CapturingMessenger


def scripted(*responses: list[ToolCallPart]) -> FunctionModel:
    calls = iter(responses)

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=list(next(calls)))

    return FunctionModel(fn)


async def run_text(
    actions: Actions, messenger: CapturingMessenger, model: FunctionModel, sleeps: list[float]
) -> None:
    async def sleep(s: float) -> None:
        sleeps.append(s)

    handler = Reply(
        agent,
        phone=PHONE,
        actions=actions,
        messenger=messenger,
        model=model,
        app_base_url="http://x",
        sleep=sleep,
    )
    trigger = await actions.submit(
        PHONE, Origin.USER, Channel.TEXT, UserMessage(text="hi, I'm Sam"), route=False
    )
    assert trigger is not None
    with agent.override(model=model):
        await handler(RunRequest(trigger=trigger, buffered=(trigger,)))


async def test_handler_runs_tools_and_delivers_bubbles(
    actions: Actions, messenger: CapturingMessenger
) -> None:
    model = scripted(
        [ToolCallPart("set_user_name", {"name": "Sam"})],
        [ToolCallPart("final_result", {"bubbles": ["hey Sam", "what should I call me?"]})],
    )
    sleeps: list[float] = []
    await run_text(actions, messenger, model, sleeps)
    assert messenger.sent == ["hey Sam", "what should I call me?"]
    assert (await actions.user(PHONE)).slots.user_name == "Sam"
    kinds = [e.kind for e in await actions.history(PHONE)]
    assert kinds == ["user_message", "slot_changed", "tool_call", "agent_message", "agent_message"]
    assert messenger.typing == [True, True, False] and sleeps[0] < sleeps[1]


async def test_zero_bubbles_is_a_hold(actions: Actions, messenger: CapturingMessenger) -> None:
    await run_text(
        actions, messenger, scripted([ToolCallPart("final_result", {"bubbles": []})]), []
    )
    assert messenger.sent == [] and messenger.typing == [False]
    assert [e.kind for e in await actions.history(PHONE)] == ["user_message"]


async def test_tools_are_filtered_by_medium(
    actions: Actions, messenger: CapturingMessenger
) -> None:
    seen: dict[str, set[str]] = {}

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["names"] = {t.name for t in info.function_tools}
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["ok"]})])

    user = await actions.user(PHONE)
    for medium, present, absent in (
        (Medium.TEXT, "start_call", "end_call"),
        (Medium.VOICE, "end_call", "start_call"),
    ):
        deps = Deps(user=user, actions=actions, messenger=messenger, medium=medium, app_base_url="")
        with agent.override(model=FunctionModel(fn)):
            await agent.run("x", deps=deps, output_type=Bubbles)
        assert present in seen["names"] and absent not in seen["names"]


async def test_instructions_include_state_and_text_tail(
    actions: Actions, messenger: CapturingMessenger
) -> None:
    captured: dict[str, str] = {}

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured["instructions"] = getattr(messages[0], "instructions", "") or ""
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": []})])

    await actions.set_slot(
        PHONE, "agent_name", "Jarvis", origin=Origin.TEXT_AGENT, channel=Channel.TEXT
    )
    deps = Deps(
        user=await actions.user(PHONE),
        actions=actions,
        messenger=messenger,
        medium=Medium.TEXT,
        app_base_url="",
    )
    with agent.override(model=FunctionModel(fn)):
        await agent.run("x", deps=deps, output_type=Bubbles)
    text = captured["instructions"]
    assert "agent_name: Jarvis" in text and "still need: user_name, help_need, gmail" in text
    assert "zero to four" in text and prompts.PERSONA[:40] in text
