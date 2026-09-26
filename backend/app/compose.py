"""Composition. The only file that sees every section.

Assembles the payload union from the sections, and builds one user's runtime:
store, state, filter, drivers, router, actor. No logic lives here.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter
from sqlalchemy import Engine

from app.actor import Actor, Hook
from app.agent.notes import note_for
from app.agent.types import Graduated, SlotChanged, ToolCall
from app.calls.hook import call_machine_hook
from app.calls.types import CallEvent
from app.events.base import Payload
from app.events.sql import SqlSink, load_user
from app.events.store import Clock, Store, utc_now
from app.gmail.types import GmailEvent
from app.routing.filter import Decider, DefaultDecider, Filter
from app.routing.router import Driver, Router
from app.routing.types import Decision, Medium
from app.text.driver import Runner, TextDriver
from app.text.types import AgentMessage, Typing, UserMessage
from app.timers import AsyncioTimers, Timers
from app.user import User, UserState
from app.voice.driver import VoiceDriver, VoiceSink
from app.voice.types import VoiceUtterance

AnyPayload = Annotated[
    UserMessage
    | AgentMessage
    | Typing
    | VoiceUtterance
    | ToolCall
    | SlotChanged
    | Graduated
    | CallEvent
    | GmailEvent
    | Decision,
    Field(discriminator="kind"),
]
PAYLOADS: TypeAdapter[Payload] = TypeAdapter(AnyPayload)  # pyright: ignore[reportArgumentType]


def load(engine: Engine, phone: str) -> User:
    sink = SqlSink(engine)
    loaded = load_user(engine, phone, payloads=PAYLOADS, state_type=UserState)
    return User(Store(phone, sink=sink, events=loaded.events), loaded.state)


def build_actor(
    user: User,
    *,
    text_runner: Runner,
    voice_sink: VoiceSink,
    decider: Decider | None = None,
    timers: Timers | None = None,
    clock: Clock = utc_now,
    hooks: list[Hook] | None = None,
) -> Actor:
    drivers: dict[Medium, Driver] = {
        Medium.TEXT: TextDriver(runner=text_runner, timers=timers or AsyncioTimers(), clock=clock),
        Medium.VOICE: VoiceDriver(sink=voice_sink, notes=note_for, clock=clock),
    }
    router = Router(user, drivers, Filter(decider or DefaultDecider()))
    return Actor(user, router, hooks=hooks if hooks is not None else [call_machine_hook])
