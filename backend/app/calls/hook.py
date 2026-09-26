"""Apply call transitions on submit, before routing, so the floor flips in the same
step the event lands (the hang-up race, docs 03)."""

from __future__ import annotations

from app.calls.machine import transition
from app.calls.types import CallEvent
from app.events.envelope import Event
from app.user import User


async def call_machine_hook(user: User, event: Event) -> bool:
    if not isinstance(event.payload, CallEvent):
        return True
    nxt = transition(user.call, event.payload, user.store.now())
    if nxt is None:
        return False  # invalid from the current phase: drop the event
    await user.set_call(nxt)
    return True
