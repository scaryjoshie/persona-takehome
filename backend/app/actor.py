"""One user, one queue, one consumer. Everything enters through `submit`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.events.base import Channel, Origin, Payload
from app.events.envelope import Event
from app.routing.router import Router
from app.user import User

log = logging.getLogger(__name__)

Hook = Callable[[User, Event], Awaitable[bool]]  # False drops the event before routing


@dataclass(frozen=True)
class Submission:
    origin: Origin
    channel: Channel
    payload: Payload
    route: bool | None  # None = the payload's default


class Actor:
    def __init__(self, user: User, router: Router, *, hooks: list[Hook] | None = None) -> None:
        self.user = user
        self.router = router
        self._hooks = hooks or []
        self._queue: asyncio.Queue[Submission | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    @property
    def phone(self) -> str:
        return self.user.phone

    def submit(
        self, origin: Origin, channel: Channel, payload: Payload, *, route: bool | None = None
    ) -> None:
        self._queue.put_nowait(Submission(origin, channel, payload, route))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._consume(), name=f"actor:{self.phone}")

    async def stop(self) -> None:
        if self._task:
            await self._queue.put(None)
            await self._task
            self._task = None

    async def drain(self) -> None:
        await self._queue.join()

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                await self._handle(item)
            except Exception:
                log.exception("actor %s: failed on %s", self.phone, item)
            finally:
                self._queue.task_done()

    async def _handle(self, s: Submission) -> None:
        store = self.user.store
        if s.payload.persists:
            event = await store.append(s.origin, s.channel, s.payload)
        else:
            event = store.transient(s.origin, s.channel, s.payload)
        for hook in self._hooks:
            if not await hook(self.user, event):
                return
        if s.route if s.route is not None else s.payload.should_route():
            await self.router.route(event)
