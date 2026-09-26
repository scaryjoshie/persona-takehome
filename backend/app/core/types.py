"""Core types: the event envelope, payload union, deltas, runs, verbs, slots.

Everything here is pure data. See docs/proposed-design/04-events-and-types.md and
05-routing-and-decider.md. Discriminated unions on `kind`; exhaustive `match` elsewhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

Origin = Literal["user", "text_agent", "voice_agent", "call", "google", "system"]
Channel = Literal["text", "voice", "system"]
Medium = Literal["text", "voice"]
Floor = Medium

CallPhase = Literal["none", "ringing", "connecting", "connected", "ended"]
CallEventPhase = Literal["ringing", "declined", "connecting", "connected", "failed", "ended"]
GmailPhase = Literal["link_sent", "connected", "failed", "skipped"]
GmailStatus = Literal["not_asked", "link_sent", "connected", "skipped"]

Verb = Literal["interrupt", "absorb", "defer"]
DecidedBy = Literal["fixed", "default", "jev", "model"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Payloads (stored in the event log)
# ---------------------------------------------------------------------------


class UserMessage(_Frozen):
    kind: Literal["user_message"] = "user_message"
    text: str


class AgentMessage(_Frozen):
    """A bubble that was actually sent on the text channel."""

    kind: Literal["agent_message"] = "agent_message"
    text: str
    via: Medium  # which handler produced it (voice can text via a tool)


class VoiceUtterance(_Frozen):
    """One turn of speech. On GPT-Live the boundary is inferred (see docs 04)."""

    kind: Literal["voice_utterance"] = "voice_utterance"
    speaker: Literal["user", "agent"]
    text: str | None
    turn_id: str
    inferred: bool = True
    item_id: str | None = None


class ToolCall(_Frozen):
    kind: Literal["tool_call"] = "tool_call"
    name: str
    args: dict[str, Any]
    result: dict[str, Any] | None = None


class SlotChanged(_Frozen):
    kind: Literal["slot_changed"] = "slot_changed"
    slot: str
    old: Any
    new: Any


class CallEvent(_Frozen):
    kind: Literal["call"] = "call"
    phase: CallEventPhase
    reason: str | None = None
    call_id: str | None = None
    initiated_by: Literal["agent", "user"] | None = None


class GmailEvent(_Frozen):
    kind: Literal["gmail"] = "gmail"
    phase: GmailPhase
    email: str | None = None


class Decision(_Frozen):
    """What the policy decided for a routed delta. Debug panel and harness read these."""

    kind: Literal["decision"] = "decision"
    trigger_kind: str
    verb: Verb | Literal["start"]
    by: DecidedBy
    confidence: float
    ms: int
    note: str | None = None


class Graduated(_Frozen):
    kind: Literal["graduated"] = "graduated"


Payload = Annotated[
    UserMessage
    | AgentMessage
    | VoiceUtterance
    | ToolCall
    | SlotChanged
    | CallEvent
    | GmailEvent
    | Decision
    | Graduated,
    Field(discriminator="kind"),
]
PAYLOAD_ADAPTER: TypeAdapter[Payload] = TypeAdapter(Payload)

PayloadKind = Literal[
    "user_message",
    "agent_message",
    "voice_utterance",
    "tool_call",
    "slot_changed",
    "call",
    "gmail",
    "decision",
    "graduated",
]

# The kinds a phone would see. Everything else is observer-only.
THREAD_KINDS: frozenset[str] = frozenset({"user_message", "agent_message", "call"})


class Event(_Frozen):
    """Envelope for one row in a user's append-only log."""

    seq: int
    ts: datetime
    origin: Origin
    channel: Channel
    payload: Payload

    @property
    def kind(self) -> str:
        return self.payload.kind


# ---------------------------------------------------------------------------
# Deltas (routable inputs). Typing is ephemeral: routed, never stored.
# ---------------------------------------------------------------------------


class Typing(_Frozen):
    kind: Literal["typing"] = "typing"
    active: bool
    seconds: float = 0.0  # how long the user has been typing, coalesced client-side


Delta = Annotated[
    UserMessage | Typing | CallEvent | GmailEvent,
    Field(discriminator="kind"),
]
DELTA_ADAPTER: TypeAdapter[Delta] = TypeAdapter(Delta)


class Inbound(_Frozen):
    """A delta plus where it came from. What producers enqueue on the actor."""

    origin: Origin
    channel: Channel
    delta: Delta


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class Slots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str | None = None
    user_name: str | None = None
    help_need: str | None = None
    gmail: GmailStatus = "not_asked"
    gmail_email: str | None = None
    graduated: bool = False

    def missing(self) -> list[str]:
        out: list[str] = []
        if self.agent_name is None:
            out.append("agent_name")
        if self.user_name is None:
            out.append("user_name")
        if self.help_need is None:
            out.append("help_need")
        if self.gmail in ("not_asked", "link_sent"):
            out.append("gmail")
        return out


class CallState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: CallPhase = "none"
    reason: str | None = None
    call_id: str | None = None
    initiated_by: Literal["agent", "user"] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Run(_Frozen):
    """One in-progress response by the floor holder. Transient, never stored."""

    medium: Medium
    started: datetime
    side_effect_in_flight: bool = False
    last_agent_turn_was_question: bool = False
    inferred: bool = False  # True on GPT-Live, where the boundary is a guess


class RoutingContext(_Frozen):
    """Everything a routing pass sees. Also what a Decision is logged from."""

    trigger: Delta
    run: Run | None
    floor: Floor
    call: CallState
    slots: Slots
    recent: tuple[Event, ...]
    now: datetime


class Verdict(_Frozen):
    verb: Verb
    confidence: float
    by: DecidedBy
    note: str | None = None
