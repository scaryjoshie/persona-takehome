from __future__ import annotations

from app.calls.machine import transition
from app.calls.types import CallEvent, CallPhase, CallState, CallTransition, Initiator
from app.user import User
from tests.conftest import FakeClock


def step(state: CallState, t: CallTransition, clock: FakeClock, **kw: object) -> CallState:
    nxt = transition(state, CallEvent(transition=t, **kw), clock())  # pyright: ignore[reportArgumentType]
    assert nxt is not None, f"{t} invalid from {state.phase}"
    return nxt


def test_happy_path(clock: FakeClock) -> None:
    s = CallState()
    s = step(s, CallTransition.RINGING, clock, initiated_by=Initiator.AGENT)
    s = step(s, CallTransition.CONNECTING, clock)
    clock.advance(2)
    s = step(s, CallTransition.CONNECTED, clock, call_id="c1")
    assert s.phase is CallPhase.CONNECTED and s.started_at == clock() and s.call_id == "c1"
    clock.advance(60)
    s = step(s, CallTransition.ENDED, clock, reason="user_hangup")
    assert s.phase is CallPhase.ENDED and s.ended_at == clock() and s.reason == "user_hangup"
    assert s.initiated_by is Initiator.AGENT


def test_invalid_transitions_return_none(clock: FakeClock) -> None:
    now = clock()
    assert transition(CallState(), CallEvent(transition=CallTransition.CONNECTED), now) is None
    assert transition(CallState(), CallEvent(transition=CallTransition.ENDED), now) is None
    ringing = step(CallState(), CallTransition.RINGING, clock)
    assert transition(ringing, CallEvent(transition=CallTransition.RINGING), now) is None
    declined = step(ringing, CallTransition.DECLINED, clock)
    assert declined.phase is CallPhase.NONE and declined.reason == "declined"


def test_failed_from_connecting_can_be_retried(clock: FakeClock) -> None:
    s = step(CallState(), CallTransition.CONNECTING, clock, initiated_by=Initiator.USER)
    s = step(s, CallTransition.FAILED, clock, reason="mic_denied")
    assert s.phase is CallPhase.NONE and s.reason == "mic_denied"
    assert step(s, CallTransition.RINGING, clock).phase is CallPhase.RINGING


async def test_user_set_call_flips_floor(user: User, clock: FakeClock) -> None:
    await user.set_call(CallState(phase=CallPhase.CONNECTED, started_at=clock()))
    assert user.floor == "voice"
    await user.set_call(CallState(phase=CallPhase.ENDED))
    assert user.floor == "text"
