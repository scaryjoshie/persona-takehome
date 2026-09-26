from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent import prompts
from app.agent.agent import agent
from app.agent.deps import Deps
from app.events.base import Channel as EventChannel
from app.events.base import Origin, Payload
from app.routing.types import Medium
from app.text.driver import RunRequest
from app.text.handler import TextHandler
from app.text.types import UserMessage
from app.user import User
from tests.conftest import FakeClock


class CapturingChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.typing: list[bool] = []

    async def send(self, phone: str, text: str) -> None:
        self.sent.append(text)

    async def set_typing(self, phone: str, active: bool) -> None:
        self.typing.append(active)


def test_every_prompt_file_is_named() -> None:
    files = {p.stem for p in (Path(prompts.__file__).parent / "prompts").glob("*.md")}
    assert files == set(prompts.ALL)
    assert all(prompts.ALL.values())


def scripted(*responses: list[ToolCallPart]) -> FunctionModel:
    calls = iter(responses)

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=list(next(calls)))

    return FunctionModel(fn)


async def run_text(
    user: User, model: FunctionModel, sleep_log: list[float]
) -> tuple[CapturingChannel, list[Payload]]:
    submitted: list[Payload] = []

    async def record(o: Origin, c: EventChannel, p: Payload) -> None:
        submitted.append(p)
        await user.store.append(o, c, p)

    def submit(o: Origin, c: EventChannel, p: Payload) -> None:
        import asyncio

        asyncio.get_running_loop().create_task(record(o, c, p))

    channel = CapturingChannel()
    deps = Deps(
        user=user, submit=submit, channel=channel, medium=Medium.TEXT, app_base_url="http://x"
    )

    async def sleep(s: float) -> None:
        sleep_log.append(s)

    handler = TextHandler(agent, deps, model=model, sleep=sleep)
    with agent.override(model=model):
        trigger = await user.store.append(
            Origin.USER, EventChannel.TEXT, UserMessage(text="hi, I'm Sam")
        )
        await handler(RunRequest(trigger=trigger, buffered=(trigger,)))
    import asyncio

    await asyncio.sleep(0)
    return channel, submitted


async def test_handler_runs_tools_and_delivers_bubbles(user: User, clock: FakeClock) -> None:
    model = scripted(
        [ToolCallPart("set_user_name", {"name": "Sam"})],
        [ToolCallPart("final_result", {"bubbles": ["hey Sam", "what should I call me?"]})],
    )
    sleeps: list[float] = []
    channel, submitted = await run_text(user, model, sleeps)
    assert channel.sent == ["hey Sam", "what should I call me?"]
    assert user.slots.user_name == "Sam"
    kinds = [p.kind_name for p in submitted]
    assert kinds.count("tool_call") == 1 and kinds.count("agent_message") == 2
    assert channel.typing == [True, True, False]
    assert sleeps[0] < sleeps[1]  # second bubble adds the gap


async def test_zero_bubbles_is_a_hold(user: User) -> None:
    model = scripted([ToolCallPart("final_result", {"bubbles": []})])
    channel, submitted = await run_text(user, model, [])
    assert channel.sent == [] and submitted == [] and channel.typing == [False]


async def test_voice_only_and_text_only_tools(user: User) -> None:
    seen: dict[str, set[str]] = {}

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["names"] = {t.name for t in info.function_tools}
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["ok"]})])

    for medium, present, absent in (
        (Medium.TEXT, "start_call", "end_call"),
        (Medium.VOICE, "end_call", "start_call"),
    ):
        deps = Deps(
            user=user,
            submit=lambda o, c, p: None,
            channel=CapturingChannel(),
            medium=medium,
            app_base_url="",
        )
        with agent.override(model=FunctionModel(fn)):
            from app.agent.agent import Reply

            await agent.run("x", deps=deps, output_type=Reply)
        assert present in seen["names"] and absent not in seen["names"]


async def test_instructions_include_state_and_text_tail(user: User) -> None:
    captured: dict[str, str] = {}

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        req = messages[0]
        captured["instructions"] = getattr(req, "instructions", "") or ""
        return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": []})])

    await user.set_slot("agent_name", "Jarvis", origin=Origin.TEXT_AGENT, channel=EventChannel.TEXT)
    deps = Deps(
        user=user,
        submit=lambda o, c, p: None,
        channel=CapturingChannel(),
        medium=Medium.TEXT,
        app_base_url="",
    )
    with agent.override(model=FunctionModel(fn)):
        from app.agent.agent import Reply

        await agent.run("x", deps=deps, output_type=Reply)
    text = captured["instructions"]
    assert "agent_name: Jarvis" in text and "still need: user_name, help_need, gmail" in text
    assert "zero to four" in text and prompts.PERSONA[:40] in text
    assert isinstance(ToolReturnPart, type)
