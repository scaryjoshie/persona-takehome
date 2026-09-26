from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from app.events.payload import Payload, Role, Turn

if TYPE_CHECKING:
    from app.routing.types import RoutingContext, Verb


class UserMessage(Payload):
    kind: Literal["user_message"] = "user_message"
    text: str

    def turn(self, at: datetime) -> Turn | None:
        return Turn(Role.USER, self.text)

    def describe(self) -> str:
        return f"the user texted: {self.text}"

    def fixed_verb(self, ctx: RoutingContext) -> Verb | None:
        from app.routing.types import Verb

        return Verb.INTERRUPT  # a message always wins


class AgentMessage(Payload):
    """A bubble that was sent. Recorded, never routed."""

    kind: Literal["agent_message"] = "agent_message"
    routes = False

    text: str
    from_call: bool = False  # sent by the voice side via a tool

    def turn(self, at: datetime) -> Turn | None:
        if self.from_call:
            return Turn(Role.NOTE, f"you texted (from the call): {self.text}")
        return Turn(Role.ASSISTANT, self.text)


class Typing(Payload):
    """Coalesced client-side. Routed, never stored."""

    kind: Literal["typing"] = "typing"
    persists = False

    active: bool
    seconds: float = 0.0

    def describe(self) -> str:
        if not self.active:
            return "the user stopped typing"
        return f"the user has been typing a text message for {self.seconds:.0f} seconds"
