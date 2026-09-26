from __future__ import annotations

from pathlib import Path

from app.core.store import Store
from app.core.types import AgentMessage, CallEvent, CallState, UserMessage
from app.db.repo import SqlSink, load_store, make_engine
from tests.core.conftest import FakeClock


async def test_write_through_and_reload(tmp_path: Path, clock: FakeClock) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    sink = SqlSink(engine)
    store = load_store(engine, "+1555", sink=sink)
    assert store.events == () and store.slots.missing() == [
        "agent_name",
        "user_name",
        "help_need",
        "gmail",
    ]

    await store.append("user", "text", UserMessage(text="hey"))
    await store.append("text_agent", "text", AgentMessage(text="hi", via="text"))
    await store.set_slot("agent_name", "Jarvis", origin="text_agent", channel="text")
    await store.set_call(CallState(phase="connected", call_id="c1", started_at=clock()))
    await store.set_floor("voice")
    await store.append("call", "system", CallEvent(phase="ended", reason="user_hangup"))

    again = load_store(engine, "+1555", sink=sink)
    assert [e.payload.kind for e in again.events] == [
        "user_message",
        "agent_message",
        "slot_changed",
        "call",
    ]
    assert again.last_seq() == 4
    assert again.slots.agent_name == "Jarvis"
    assert again.call.phase == "connected" and again.call.call_id == "c1"
    assert again.call.started_at is not None and again.call.started_at.tzinfo is not None
    assert again.floor == "voice"
    e = await again.append("user", "text", UserMessage(text="after restart"))
    assert e.seq == 5


async def test_users_are_isolated(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    sink = SqlSink(engine)
    a = load_store(engine, "a", sink=sink)
    b = load_store(engine, "b", sink=sink)
    await a.append("user", "text", UserMessage(text="only a"))
    assert load_store(engine, "b", sink=sink).events == ()
    assert len(load_store(engine, "a", sink=sink).events) == 1
    assert isinstance(b, Store)
