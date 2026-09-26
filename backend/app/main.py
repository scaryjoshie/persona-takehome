"""Entry point. The only file that sees every section: assembles the payload union
and wires one process's live, actions, and per-user responders. The FastAPI app
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
from app.agent.call_notes import call_note
from app.agent.events import Graduated, SlotChanged, ToolCall
from app.calls.events import CallEvent
from app.database import SessionFactory, utc_now
from app.events.payload import Payload
from app.gmail.events import GmailEvent
from app.routing.deciders import Decider, DefaultDecider, JevDecider
from app.routing.filter import Filter
from app.routing.responder import Responder
from app.routing.router import Router
from app.routing.types import Decision, Medium
from app.settings import Settings
from app.text.events import AgentMessage, Typing, UserMessage
from app.text.messenger import Messenger
from app.text.reply import Reply
from app.text.responder import TextResponder
from app.timers import AsyncioTimers, Timers
from app.users.live import LiveUser, LiveUsers
from app.voice.events import VoiceUtterance
from app.voice.responder import VoiceResponder

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


def decider_from(settings: Settings) -> Decider:
    """Jev if an OpenRouter key is configured, otherwise the fixed defaults."""
    if settings.openrouter_api_key is None:
        return DefaultDecider()
    return JevDecider(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.jev_model,
        fallback=DefaultDecider(),
    )


class _LiveVoice:
    """The voice responder's sink: forwards to the live's live session, if any."""

    def __init__(self, live: LiveUser) -> None:
        self._runtime = live

    async def send(self, text: str, *, speak: bool) -> None:
        if self._runtime.voice is not None:
            await self._runtime.voice.send(text, speak=speak)


@dataclass
class App:
    actions: Actions
    live_users: LiveUsers


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

    def make_live_user(phone: str) -> LiveUser:
        handler = Reply(
            agent,
            phone=phone,
            actions=holder[0],
            messenger=messenger,
            model=model,
            app_base_url=app_base_url,
        )
        responders: dict[Medium, Responder] = {
            Medium.TEXT: TextResponder(
                runner=handler, timers=timers or AsyncioTimers(), clock=clock
            ),
        }
        live = LiveUser(phone, responders)
        responders[Medium.VOICE] = VoiceResponder(
            sink=_LiveVoice(live), notes=call_note, clock=clock
        )
        return live

    live_users = LiveUsers(make_live_user)
    actions = Actions(db, live_users, router, payloads=PAYLOADS, clock=clock)
    holder.append(actions)
    return App(actions=actions, live_users=live_users)
