from __future__ import annotations

from app.database import SessionFactory
from app.events import service as events
from app.events.payload import Channel, Origin
from app.google.events import GmailPhase
from app.text.events import AgentMessage, UserMessage
from app.users import service as users
from app.voice.call_state import CallPhase, CallState
from tests.conftest import PHONE, FakeClock


async def test_append_assigns_seq_per_user_and_lists_in_order(
    db: SessionFactory, clock: FakeClock
) -> None:
    async with db() as s, s.begin():
        await users.ensure_user(s, PHONE, now=clock())
        await users.ensure_user(s, "+2", now=clock())
        a = await events.append(
            s, PHONE, Origin.USER, Channel.TEXT, UserMessage(text="hey"), ts=clock()
        )
        clock.advance(1)
        b = await events.append(
            s, PHONE, Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="hi"), ts=clock()
        )
        other = await events.append(
            s, "+2", Origin.USER, Channel.TEXT, UserMessage(text="x"), ts=clock()
        )
    assert (a.seq, b.seq, other.seq) == (1, 2, 1)
    async with db() as s:
        listed = await events.list_events(s, PHONE)
        tail = await events.list_events(s, PHONE, limit=1)
    assert [e.kind for e in listed] == ["user_message", "agent_message"]
    assert listed[1].ts > listed[0].ts and listed[0].ts.tzinfo is not None
    assert [e.seq for e in tail] == [2]


async def test_user_state_round_trips(db: SessionFactory, clock: FakeClock) -> None:
    async with db() as s, s.begin():
        u = await users.ensure_user(s, PHONE, now=clock())
        assert u.slots.missing() == ("agent_name", "user_name", "help_need", "gmail")
        await users.set_slots(s, PHONE, agent_name="Jarvis")
        await users.set_slots(s, PHONE, gmail=GmailPhase.LINK_SENT)
        call = CallState(phase=CallPhase.CONNECTED, call_id="c1", started_at=clock())
        await users.set_call(s, PHONE, call)
        u = await users.ensure_user(s, PHONE, now=clock())
    assert u.slots.agent_name == "Jarvis" and u.slots.gmail is GmailPhase.LINK_SENT
    assert u.floor == "voice" and u.call.call_id == "c1"
    async with db() as s:
        again = await users.get_user(s, PHONE)
    assert again == u
    assert (
        again is not None
        and again.call.started_at is not None
        and again.call.started_at.tzinfo is not None
    )
