from __future__ import annotations

from app.calls.machine import transition
from app.calls.types import CallEvent, CallPhase, CallState, CallTransition, Initiator
from tests.conftest import FakeClock


def step(state: CallState, t: CallTransition, clock: FakeClock, **kw: object) -> CallState:
    nxt = transition(state, CallEvent(transition=t, **kw), clock())  # pyright: ignore[reportArgumentType]
    assert nxt is not None, f"{t} invalid from {state.phase}"
    return nxt


def test_happy_path(clock: FakeClock) -> None:
    s = step(CallState(), CallTransition.RINGING, clock, initiated_by=Initiator.AGENT)
    s = step(s, CallTransition.CONNECTING, clock)
    clock.advance(2)
    s = step(s, CallTransition.CONNECTED, clock, call_id="c1")
    assert s.phase is CallPhase.CONNECTED and s.started_at == clock() and s.call_id == "c1"
    clock.advance(60)
    s = step(s, CallTransition.ENDED, clock, reason="user_hangup")
    assert (
        s.phase is CallPhase.ENDED and s.ended_at == clock() and s.initiated_by is Initiator.AGENT
    )


def test_invalid_transitions_return_none(clock: FakeClock) -> None:
    now = clock()
    assert transition(CallState(), CallEvent(transition=CallTransition.CONNECTED), now) is None
    assert transition(CallState(), CallEvent(transition=CallTransition.ENDED), now) is None
    ringing = step(CallState(), CallTransition.RINGING, clock)
    assert transition(ringing, CallEvent(transition=CallTransition.RINGING), now) is None
    assert step(ringing, CallTransition.DECLINED, clock).phase is CallPhase.NONE


def test_failed_from_connecting_can_be_retried(clock: FakeClock) -> None:
    s = step(CallState(), CallTransition.CONNECTING, clock, initiated_by=Initiator.USER)
    s = step(s, CallTransition.FAILED, clock, reason="mic_denied")
    assert s.phase is CallPhase.NONE and s.reason == "mic_denied"
    assert step(s, CallTransition.RINGING, clock).phase is CallPhase.RINGING


def test_only_outcomes_route() -> None:
    assert CallEvent(transition=CallTransition.ENDED).should_route()
    assert CallEvent(transition=CallTransition.DECLINED).should_route()
    assert not CallEvent(transition=CallTransition.RINGING).should_route()
    assert not CallEvent(transition=CallTransition.CONNECTED).should_route()
