from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.database import SessionFactory, create_schema, make_engine, make_sessions
from app.events.decision import Decision
from app.events.event import Event
from app.events.payload import Channel, Origin, Payload
from app.llm import Models
from app.main import App, assemble
from app.pipeline import Context, Pipeline
from app.text.events import UserMessage
from app.text.reply import Replier
from app.text.responder import TextResponder
from app.users.user import Medium, User

PHONE = "15550001111"


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class FakeTimers:
    """Records timers; tests fire them explicitly and the clock jumps to the due time."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._entries: list[tuple[datetime, Callable[[], None], bool]] = []

    def call_later(self, delay: float, cb: Callable[[], None]) -> None:
        self._entries.append((self.clock() + timedelta(seconds=delay), cb, False))

    @property
    def pending(self) -> list[float]:
        now = self.clock()
        return sorted(
            round((due - now).total_seconds(), 3) for due, _, done in self._entries if not done
        )

    def fire_next(self) -> None:
        live = [(i, due) for i, (due, _, done) in enumerate(self._entries) if not done]
        assert live, "nothing scheduled"
        i, due = min(live, key=lambda x: x[1])
        cb = self._entries[i][1]
        self._entries[i] = (due, cb, True)
        self.clock.t = max(self.clock.t, due)
        cb()


class CapturingMessenger:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.typing: list[bool] = []

    async def send(self, phone: str, text: str) -> None:
        self.sent.append(text)

    async def set_typing(self, phone: str, active: bool) -> None:
        self.typing.append(active)


class FakeResponder:
    """Records what it was handed and decides nothing."""

    def __init__(self) -> None:
        self.handled: list[str] = []

    async def handle(self, event: Event, user: User, ctx: Context) -> Decision | None:
        self.handled.append(event.kind)
        return Decision(trigger_kind=event.kind, verb="seen")


async def reply_hi(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("final_result", {"bubbles": ["hi"]})])


async def no_sleep(seconds: float) -> None:
    return None


async def settle(pipeline: Pipeline) -> None:
    """Wait until background work (replies, delayed submits) has finished."""
    for _ in range(50):
        pending = [t for t in pipeline._background if not t.done()]  # pyright: ignore[reportPrivateUsage]
        if not pending:
            await asyncio.sleep(0)
            if not [t for t in pipeline._background if not t.done()]:  # pyright: ignore[reportPrivateUsage]
                return
        await asyncio.gather(*pending, return_exceptions=True)


def ev(
    payload: Payload, origin: Origin = Origin.USER, channel: Channel = Channel.TEXT, seq: int = 0
) -> Event:
    return Event(
        seq=seq,
        ts=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        origin=origin,
        channel=channel,
        payload=payload,
    )


def user_text(text: str) -> Event:
    return ev(UserMessage(text=text))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def timers(clock: FakeClock) -> FakeTimers:
    return FakeTimers(clock)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await create_schema(engine)
    yield make_sessions(engine)
    await engine.dispose()


@pytest.fixture
def messenger() -> CapturingMessenger:
    return CapturingMessenger()


@pytest.fixture
def app(
    db: SessionFactory, messenger: CapturingMessenger, clock: FakeClock, timers: FakeTimers
) -> App:
    built = assemble(
        db=db,
        messenger=messenger,
        models=Models.same(FunctionModel(reply_hi)),
        app_base_url="http://x",
        timers=timers,
        clock=clock,
        web_search=False,
    )
    built.pipeline.responders[Medium.TEXT] = TextResponder(Replier(built.env, sleep=no_sleep))
    return built


@pytest.fixture
def pipeline(app: App) -> Pipeline:
    return app.pipeline
