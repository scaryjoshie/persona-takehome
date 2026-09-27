"""Events the agent itself produces: a slot it wrote, a tool it called, graduation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.agent.slots import TzSource
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
    """A tool the agent (or a background job) used, kept compact: no raw payloads, no secrets."""

    kind: Literal["tool_call"] = "tool_call"
    routes = False

    name: str  # the action, like search_email
    args: dict[str, Any]
    result: dict[str, Any] | None = None
    # What it showed or did, kept short, so later turns can still refer to it (email headers,
    # calendar events). Bodies aren't kept: it can open an email again by its id.
    shown: str | None = None
    app: str | None = None  # the service it used ("google"), or None for the agent's own tools
    ok: bool = True

    def turn(self, at: datetime) -> Turn | None:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        result = "" if self.result is None else f" → {self.result}"
        failed = " (failed)" if not self.ok else ""
        shown = f", and saw:\n{self.shown}" if self.shown else ""
        return Turn(Role.NOTE, f"you called {self.name}({args}){result}{failed}{shown}")


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
    """Their browser's timezone. No longer sent or used: a texting assistant couldn't know it.
    Kept so older rows still load."""

    kind: Literal["device_timezone"] = "device_timezone"
    routes = False

    tz: str


class TimezoneLearned(Payload):
    """Where they are, in time: from their Google Calendar's setting, or because they said. What
    they said wins over the calendar; the pipeline drops a calendar zone after one they said."""

    kind: Literal["timezone_learned"] = "timezone_learned"
    routes = False

    tz: str  # IANA, like America/Denver
    source: TzSource

    def turn(self, at: datetime) -> Turn | None:
        how = "they told you" if self.source is TzSource.SAID else "from their Google Calendar"
        return Turn(Role.NOTE, f"their timezone is {self.tz} ({how})")


class CallOptOut(Payload):
    """They'd rather not talk on the phone. The agent stops offering a call."""

    kind: Literal["call_opt_out"] = "call_opt_out"
    routes = False

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.NOTE, "they'd rather not do a call")
