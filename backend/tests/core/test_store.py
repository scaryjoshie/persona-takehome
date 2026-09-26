from __future__ import annotations

from app.core.store import Store
from app.core.types import AgentMessage, CallState, Decision, Event, Floor, Slots, UserMessage
from tests.core.conftest import FakeClock


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.states: list[tuple[Slots, CallState, Floor]] = []

    async def on_event(self, phone: str, event: Event) -> None:
        self.events.append(event)

    async def on_state(self, phone: str, slots: Slots, call: CallState, floor: Floor) -> None:
        self.states.append((slots.model_copy(), call.model_copy(), floor))


async def test_append_assigns_monotonic_seq_and_writes_through(clock: FakeClock) -> None:
    sink = RecordingSink()
    store = Store("+15551234567", clock=clock, sink=sink)
    a = await store.append("user", "text", UserMessage(text="hey"))
    clock.advance(1)
    b = await store.append("text_agent", "text", AgentMessage(text="hi!", via="text"))
    assert (a.seq, b.seq) == (1, 2)
    assert b.ts > a.ts
    assert [e.seq for e in sink.events] == [1, 2]
    assert store.last_seq() == 2


async def test_rehydrate_continues_seq(clock: FakeClock) -> None:
    first = Store("p", clock=clock)
    await first.append("user", "text", UserMessage(text="one"))
    await first.append("user", "text", UserMessage(text="two"))
    second = Store("p", clock=clock, events=first.events)
    e = await second.append("user", "text", UserMessage(text="three"))
    assert e.seq == 3
    assert [x.payload.kind for x in second.events] == ["user_message"] * 3


async def test_set_slot_is_idempotent_and_logs_change(clock: FakeClock) -> None:
    sink = RecordingSink()
    store = Store("p", clock=clock, sink=sink)
    assert await store.set_slot("user_name", "Siobhan", origin="voice_agent", channel="voice")
    assert not await store.set_slot("user_name", "Siobhan", origin="text_agent", channel="text")
    assert store.slots.user_name == "Siobhan"
    kinds = [e.payload.kind for e in store.events]
    assert kinds == ["slot_changed"]
    assert store.slots.missing() == ["agent_name", "help_need", "gmail"]
    assert len(sink.states) == 1


async def test_subscribe_with_kind_filter(clock: FakeClock) -> None:
    store = Store("p", clock=clock)
    seen_all: list[str] = []
    seen_thread: list[str] = []
    store.subscribe(lambda e: seen_all.append(e.kind))
    unsub = store.subscribe(
        lambda e: seen_thread.append(e.kind), kinds={"user_message", "agent_message"}
    )
    await store.append("user", "text", UserMessage(text="x"))
    await store.append(
        "system",
        "system",
        Decision(trigger_kind="user_message", verb="start", by="fixed", confidence=1.0, ms=0),
    )
    unsub()
    await store.append("text_agent", "text", AgentMessage(text="y", via="text"))
    assert seen_all == ["user_message", "decision", "agent_message"]
    assert seen_thread == ["user_message"]


async def test_async_subscriber_is_awaited(clock: FakeClock) -> None:
    store = Store("p", clock=clock)
    got: list[int] = []

    async def sub(e: Event) -> None:
        got.append(e.seq)

    store.subscribe(sub)
    await store.append("user", "text", UserMessage(text="x"))
    assert got == [1]
