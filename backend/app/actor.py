"""Per-user actor: one queue, one consumer, and everything per-user behind it.

Producers (browser socket, voice handler, OAuth callback, timers) only enqueue.
The consumer appends to the store, applies call transitions, then routes. That
is what gives "before" and "after" a meaning (docs 03, 08).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.calls import apply_call_event
from app.core.router import Head, Router
from app.core.store import Store
from app.core.types import CallEvent, Inbound, Medium, Typing

log = logging.getLogger(__name__)


class Actor:
    def __init__(self, store: Store, heads: dict[Medium, Head], router: Router) -> None:
        self.store = store
        self.heads = heads
        self.router = router
        self._queue: asyncio.Queue[Inbound | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    @property
    def phone(self) -> str:
        return self.store.phone

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._consume(), name=f"actor:{self.phone}")

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    def enqueue(self, inbound: Inbound) -> None:
        self._queue.put_nowait(inbound)

    async def drain(self) -> None:
        """Test helper: wait until everything enqueued so far has been processed."""
        await self._queue.join()

    async def _consume(self) -> None:
        while True:
            inbound = await self._queue.get()
            try:
                if inbound is None:
                    return
                await self._handle(inbound)
            except Exception:
                log.exception("actor %s: failed handling %s", self.phone, inbound)
            finally:
                self._queue.task_done()

    async def _handle(self, inbound: Inbound) -> None:
        delta = inbound.delta
        if isinstance(delta, CallEvent):
            if not await apply_call_event(self.store, delta):
                log.info(
                    "actor %s: ignored call event %s from %s",
                    self.phone,
                    delta.phase,
                    self.store.call.phase,
                )
                return
        if not isinstance(delta, Typing):  # typing is ephemeral: routed, never stored
            await self.store.append(inbound.origin, inbound.channel, delta)
        await self.router.route(inbound)
