"""Timer abstraction so responders can be tested without sleeping."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class Timers(Protocol):
    def call_later(self, delay: float, cb: Callable[[], None]) -> TimerHandle: ...


class AsyncioTimers:
    def call_later(self, delay: float, cb: Callable[[], None]) -> TimerHandle:
        return asyncio.get_running_loop().call_later(delay, cb)
