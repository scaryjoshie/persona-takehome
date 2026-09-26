from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.calls.types import CallEvent, CallPhase, CallState, CallTransition
from app.compose import PAYLOADS, load
from app.events.base import Channel, Origin
from app.events.envelope import Event
from app.events.sql import make_engine
from app.events.store import Store
from app.routing.types import DecidedBy, Decision, Verb
from app.text.types import AgentMessage, UserMessage
from app.user import User, UserState
from tests.conftest import FakeClock


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.states: list[BaseModel] = []

    async def on_event(self, phone: str, event: Event) -> None:
        self.events.append(event)

    async def on_state(self, phone: str, state: BaseModel) -> None:
        self.states.append(state)


async def test_append_is_monotonic_and_writes_through(clock: FakeClock) -> None:
    sink = RecordingSink()
    store = Store("p", clock=clock, sink=sink)
    a = await store.append(Origin.USER, Channel.TEXT, UserMessage(text="hey"))
    clock.advance(1)
    b = await store.append(Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="hi"))
    assert (a.seq, b.seq) == (1, 2) and b.ts > a.ts
    assert [e.seq for e in sink.events] == [1, 2]


async def test_transient_events_are_not_logged(store: Store) -> None:
    e = store.transient(Origin.USER, Channel.TEXT, UserMessage(text="x"))
    assert e.seq == 0 and store.events == ()


async def test_rehydrated_store_continues_seq(clock: FakeClock) -> None:
    first = Store("p", clock=clock)
    await first.append(Origin.USER, Channel.TEXT, UserMessage(text="one"))
    second = Store("p", clock=clock, events=first.events)
    assert (await second.append(Origin.USER, Channel.TEXT, UserMessage(text="two"))).seq == 2


async def test_subscribe_with_kind_filter(store: Store) -> None:
    all_kinds: list[str] = []
    thread: list[str] = []
    store.subscribe(lambda e: all_kinds.append(e.kind))
    unsub = store.subscribe(lambda e: thread.append(e.kind), kinds={"user_message"})
    await store.append(Origin.USER, Channel.TEXT, UserMessage(text="x"))
    await store.append(
        Origin.SYSTEM,
        Channel.SYSTEM,
        Decision(trigger_kind="x", verb=Verb.START, by=DecidedBy.FIXED, confidence=1, ms=0),
    )
    unsub()
    await store.append(Origin.USER, Channel.TEXT, UserMessage(text="y"))
    assert all_kinds == ["user_message", "decision", "user_message"]
    assert thread == ["user_message"]


async def test_set_slot_is_idempotent_and_logged(user: User) -> None:
    assert await user.set_slot(
        "user_name", "Siobhan", origin=Origin.VOICE_AGENT, channel=Channel.VOICE
    )
    assert not await user.set_slot(
        "user_name", "Siobhan", origin=Origin.TEXT_AGENT, channel=Channel.TEXT
    )
    assert [e.kind for e in user.store.events] == ["slot_changed"]
    assert user.slots.missing() == ("agent_name", "help_need", "gmail")


def test_payload_union_round_trips() -> None:
    p = PAYLOADS.validate_python({"kind": "call", "transition": "ended", "reason": "user_hangup"})
    assert isinstance(p, CallEvent) and p.transition is CallTransition.ENDED
    assert p.model_dump(mode="json")["transition"] == "ended"


async def test_sqlite_write_through_and_reload(tmp_path: Path, clock: FakeClock) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    user = load(engine, "+1555")
    assert user.store.events == () and user.state == UserState()
    await user.store.append(Origin.USER, Channel.TEXT, UserMessage(text="hey"))
    await user.set_slot("agent_name", "Jarvis", origin=Origin.TEXT_AGENT, channel=Channel.TEXT)
    await user.set_call(CallState(phase=CallPhase.CONNECTED, call_id="c1", started_at=clock()))
    await user.store.append(Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.ENDED))

    again = load(engine, "+1555")
    assert [e.kind for e in again.store.events] == ["user_message", "slot_changed", "call"]
    assert again.slots.agent_name == "Jarvis"
    assert again.call.call_id == "c1" and again.call.started_at is not None
    assert again.call.started_at.tzinfo is not None
    assert again.floor == "voice"
    assert (await again.store.append(Origin.USER, Channel.TEXT, UserMessage(text="z"))).seq == 4
    assert load(engine, "+1666").store.events == ()  # users are isolated
