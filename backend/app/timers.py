"""Time, injectable: a clock and a timer scheduler, so responders test without sleeping."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

Clock = Callable[[], datetime]


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class Timers(Protocol):
    def call_later(self, delay: float, cb: Callable[[], None]) -> TimerHandle: ...


class AsyncioTimers:
    def call_later(self, delay: float, cb: Callable[[], None]) -> TimerHandle:
        return asyncio.get_running_loop().call_later(delay, cb)
