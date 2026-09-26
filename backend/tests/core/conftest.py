from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


class FakeClock:
    """Deterministic clock. Tests advance it explicitly."""

    def __init__(self, start: datetime | None = None) -> None:
        self.t = start or datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
