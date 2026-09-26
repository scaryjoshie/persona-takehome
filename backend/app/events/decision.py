"""What a medium decided about a routed event. Logged for the debug panel and tests.

Each medium has its own vocabulary, so `verb` is a plain string: the text medium says
things like "schedule", "wait", "reply"; the voice medium "interrupt", "absorb", "defer".
"""

from __future__ import annotations

from typing import Literal

from app.events.payload import Payload


class Decision(Payload):
    kind: Literal["decision"] = "decision"
    routes = False

    trigger_kind: str
    verb: str
    by: str = "rule"  # "rule", "jev", or "fallback"
    confidence: float | None = None
    note: str | None = None
