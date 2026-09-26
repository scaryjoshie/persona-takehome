"""Entry point. The only file that sees every section: assembles the payload union
and wires one process's runtime, actions, and per-user drivers. The FastAPI app
will live here too. No logic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import Field, TypeAdapter
from pydantic_ai.models import Model

from app.actions import Actions
from app.agent.agent import agent
from app.agent.notes import note_for
from app.agent.types import Graduated, SlotChanged, ToolCall
from app.calls.types import CallEvent
from app.database import SessionFactory, utc_now
from app.events.base import Payload
from app.gmail.types import GmailEvent
from app.routing.filter import Decider, DefaultDecider, Filter
from app.routing.router import Driver, Router
from app.routing.types import Decision, Medium
from app.runtime import Runtime, Runtimes
from app.text.driver import TextDriver
from app.text.handler import TextHandler
from app.text.messenger import Messenger
from app.text.types import AgentMessage, Typing, UserMessage
from app.timers import AsyncioTimers, Timers
from app.voice.driver import VoiceDriver
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


class _RuntimeVoice:
    """The voice driver's sink: forwards to the runtime's live session, if any."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    async def send(self, text: str, *, speak: bool) -> None:
        if self._runtime.voice is not None:
            await self._runtime.voice.send(text, speak=speak)


@dataclass
class App:
    actions: Actions
    runtimes: Runtimes


def build_app(
    *,
    db: SessionFactory,
    messenger: Messenger,
    model: Model,
    app_base_url: str,
    decider: Decider | None = None,
    timers: Timers | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> App:
    router = Router(Filter(decider or DefaultDecider()), clock=clock)
    holder: list[Actions] = []

    def make_runtime(phone: str) -> Runtime:
        handler = TextHandler(
            agent,
            phone=phone,
            actions=holder[0],
            messenger=messenger,
            model=model,
            app_base_url=app_base_url,
        )
        drivers: dict[Medium, Driver] = {
            Medium.TEXT: TextDriver(runner=handler, timers=timers or AsyncioTimers(), clock=clock),
        }
        runtime = Runtime(phone, drivers)
        drivers[Medium.VOICE] = VoiceDriver(
            sink=_RuntimeVoice(runtime), notes=note_for, clock=clock
        )
        return runtime

    runtimes = Runtimes(make_runtime)
    actions = Actions(db, runtimes, router, payloads=PAYLOADS, clock=clock)
    holder.append(actions)
    return App(actions=actions, runtimes=runtimes)
