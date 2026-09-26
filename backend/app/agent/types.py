"""The onboarding domain: slots and the agent's own recorded actions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.events.base import Payload, Role, Turn
from app.gmail.types import GmailPhase


class Slots(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail: GmailPhase | None = None  # None = not asked yet
    gmail_email: str | None = None
    graduated: bool = False

    def missing(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.agent_name is None:
            out.append("agent_name")
        if self.user_name is None:
            out.append("user_name")
        if self.help_need is None:
            out.append("help_need")
        if self.gmail in (None, GmailPhase.LINK_SENT):
            out.append("gmail")
        return tuple(out)


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
