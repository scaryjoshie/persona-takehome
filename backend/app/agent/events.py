"""Events the agent itself produces: a slot it wrote, a tool it called, graduation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.events.payload import Payload, Role, Turn

NamedSlot = Literal["agent_name", "user_name", "help_need"]


class SlotChanged(Payload):
    """The agent recorded a name or the help need. The pipeline fills in `old` and drops the
    event if the value did not change."""

    kind: Literal["slot_changed"] = "slot_changed"
    routes = False

    slot: NamedSlot
    new: str
    old: str | None = None


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
