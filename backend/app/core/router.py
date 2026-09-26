"""Route an inbound delta to the floor holder. See docs 03 and 05.

1. The store has already appended the delta (store before route).
2. Drop it if its origin is the floor holder (loop guard; record-only by construction).
3. Pick the head by floor.
4. No run: the head starts one.
5. Run exists: the policy picks a verb; the head applies it.
6. Log a Decision event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.core.deciders import Decider
from app.core.policy import policy
from app.core.store import Store
from app.core.types import (
    Decision,
    Floor,
    Inbound,
    Medium,
    Origin,
    RoutingContext,
    Run,
    Verb,
)

RECENT_FOR_DECIDER = 12

FLOOR_HOLDER_ORIGIN: dict[Floor, Origin] = {"text": "text_agent", "voice": "voice_agent"}


class Head(Protocol):
    medium: Medium

    @property
    def run(self) -> Run | None: ...
    async def start(self, inbound: Inbound) -> None: ...
    async def apply(self, verb: Verb, inbound: Inbound) -> None: ...


class Router:
    def __init__(self, store: Store, heads: dict[Medium, Head], decider: Decider) -> None:
        self._store = store
        self._heads = heads
        self._decider = decider

    async def route(self, inbound: Inbound) -> Decision | None:
        store = self._store
        floor = store.floor
        if inbound.origin == FLOOR_HOLDER_ORIGIN[floor]:
            return None  # the floor holder's own output is recorded, never routed back

        head = self._heads[floor]
        started = store.now()
        if head.run is None:
            await head.start(inbound)
            decision = Decision(
                trigger_kind=inbound.delta.kind,
                verb="start",
                by="fixed",
                confidence=1.0,
                ms=_ms_since(store, started),
                note=f"no active run on {floor}",
            )
        else:
            ctx = RoutingContext(
                trigger=inbound.delta,
                run=head.run,
                floor=floor,
                call=store.call,
                slots=store.slots,
                recent=store.recent(RECENT_FOR_DECIDER),
                now=started,
            )
            verdict = await policy(ctx, self._decider)
            await head.apply(verdict.verb, inbound)
            decision = Decision(
                trigger_kind=inbound.delta.kind,
                verb=verdict.verb,
                by=verdict.by,
                confidence=verdict.confidence,
                ms=_ms_since(store, started),
                note=verdict.note,
            )
        await store.append("system", "system", decision)
        return decision


def _ms_since(store: Store, then: datetime) -> int:
    return int((store.now() - then).total_seconds() * 1000)
