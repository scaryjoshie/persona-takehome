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


class ContactCard(Payload):
    """The agent's contact card (a .vcf), sent whenever its name is set or changed. The
    user's phone only shows the new name once they tap to save it (ContactSaved)."""

    kind: Literal["contact_card"] = "contact_card"
    routes = False

    name: str

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, f"your contact card went out as {self.name}")


class ContactSaved(Payload):
    """The user saved the agent's contact card. Sets what their phone calls the agent.

    It happens on their phone, so the agent never learns of it: not routed, not in any
    prompt. Only the phone's own display uses it."""

    kind: Literal["contact_saved"] = "contact_saved"
    routes = False

    name: str


class DeviceTimezone(Payload):
    """Their device's timezone (IANA, like America/Los_Angeles), sent by their browser.
    Calendar times and "what time is it for them" use it; the agent never sees it as an event."""

    kind: Literal["device_timezone"] = "device_timezone"
    routes = False

    tz: str


class CallOptOut(Payload):
    """They'd rather not talk on the phone. The agent stops offering a call."""

    kind: Literal["call_opt_out"] = "call_opt_out"
    routes = False

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, "they'd rather not do a call")
