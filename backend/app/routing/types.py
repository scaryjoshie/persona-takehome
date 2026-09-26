from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.calls.state import CallState
from app.events.event import Event
from app.events.payload import Payload


class Medium(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class Verb(StrEnum):
    START = "start"  # no run existed; the responder started one (never returned by the filter)
    INTERRUPT = "interrupt"
    ABSORB = "absorb"
    DEFER = "defer"


class DecidedBy(StrEnum):
    FIXED = "fixed"  # the event's own fixed verb
    DEFAULT = "default"  # no decider configured; the kind's default
    JEV = "jev"
    MODEL = "model"


class Run(BaseModel):
    """One in-progress response by the floor holder. Transient, never stored."""

    model_config = ConfigDict(frozen=True)

    medium: Medium
    started: datetime
    side_effect_in_flight: bool = False
    last_agent_turn_was_question: bool = False
    inferred: bool = False  # True on GPT-Live, where the boundary is a guess


class RoutingContext(BaseModel):
    """Everything a routing pass sees."""

    model_config = ConfigDict(frozen=True)

    trigger: Event
    run: Run | None
    floor: Medium
    call: CallState
    still_missing: tuple[str, ...]
    recent: tuple[Event, ...]
    now: datetime


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    verb: Verb
    confidence: float
    by: DecidedBy
    note: str | None = None


class Decision(Payload):
    """Logged for every routed event. The debug panel and the harness read these."""

    kind: Literal["decision"] = "decision"
    routes = False

    trigger_kind: str
    verb: Verb
    by: DecidedBy
    confidence: float
    ms: int
    note: str | None = None
