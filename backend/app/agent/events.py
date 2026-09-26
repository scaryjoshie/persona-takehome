"""Events the agent itself produces: a slot it wrote, a tool it called, graduation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.events.payload import Payload, Role, Turn


class SlotChanged(Payload):
    kind: Literal["slot_changed"] = "slot_changed"
    routes = False

    slot: str
    old: Any
    new: Any


class ToolCall(Payload):
    kind: Literal["tool_call"] = "tool_call"
    routes = False

    name: str
    args: dict[str, Any]
    result: dict[str, Any] | None = None

    def turn(self, at: datetime) -> Turn | None:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        result = "" if self.result is None else f" → {self.result}"
        return Turn(Role.NOTE, f"you called {self.name}({args}){result}")


class Graduated(Payload):
    kind: Literal["graduated"] = "graduated"
    routes = False

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, "user graduated to the main experience")
