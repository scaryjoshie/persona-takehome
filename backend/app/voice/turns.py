"""Stitch GPT-Live's turns back together.

Live infers turn boundaries from silence, so one spoken sentence often arrives as several
turns ("Hi" / ". My name is Siobhan"), and the agent's reply splits around tool activity
("what's" / "one" / "kind"). Recorded as-is, the history reads as noise and the back office
runs on half-sentences. The joiner keeps the latest finished turn open for a moment: another
turn from the same speaker within `quiet` seconds joins it under the same id (so the browser
shows one caption), and the other speaker starting, or the quiet running out, closes it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.voice.events import Speaker


@dataclass(frozen=True)
class Line:
    speaker: Speaker
    turn_id: str
    text: str


def join(before: str, after: str) -> str:
    before, after = before.strip(), after.strip()
    if not before or not after:
        return before or after
    return f"{before}{'' if after[0] in ',.;:!?' else ' '}{after}"


class TurnJoiner:
    def __init__(self, *, quiet: float = 1.2, clock: Callable[[], float] = time.monotonic):
        self.pending: Line | None = None  # the latest finished turn, not yet recorded
        self._quiet = quiet
        self._clock = clock
        self._last_final = 0.0
        self._joining: str | None = None  # the raw turn in progress that is joining `pending`
        self._joined: Line | None = None  # `pending` plus that turn's words so far

    def caption(self, speaker: Speaker, raw_id: str, text: str) -> tuple[Line, Line | None]:
        """A live caption. Returns what to show, and a turn it closed, to record."""
        closed = None
        if self.pending is not None and not self._joins(speaker, raw_id):
            closed, self.pending = self.pending, None
        if self.pending is None:
            return Line(speaker, raw_id, text.strip()), closed
        self._joining = raw_id
        self._joined = Line(speaker, self.pending.turn_id, join(self.pending.text, text))
        return self._joined, closed

    def final(self, speaker: Speaker, raw_id: str, text: str) -> tuple[Line, Line | None]:
        """A turn finished. It stays open (pending) until `due` or the other speaker."""
        line, closed = self.caption(speaker, raw_id, text)
        self.pending, self._joining, self._joined = line, None, None
        self._last_final = self._clock()
        return line, closed

    def due(self) -> Line | None:
        """The pending turn, if its speaker has been quiet long enough."""
        if self.pending is None or self._joining is not None or not self._quiet_enough():
            return None
        return self.close()

    def close(self) -> Line | None:
        """Close the open turn now, including words of a joining turn cut off mid-sentence."""
        line = self._joined or self.pending
        self.pending, self._joining, self._joined = None, None, None
        return line

    def _joins(self, speaker: Speaker, raw_id: str) -> bool:
        assert self.pending is not None
        if self.pending.speaker is not speaker:
            return False
        return raw_id == self._joining or not self._quiet_enough()

    def _quiet_enough(self) -> bool:
        return self._clock() - self._last_final >= self._quiet
