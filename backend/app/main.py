"""Entry point: the one place that sees every section. Two jobs:

- PAYLOADS: every event kind in one union, so rows read back from SQLite become the
  right class. Adding an event kind means adding it here.
- build_app: wires the pieces for this process. The FastAPI app will live here too.
"""

from __future__ import annotations

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
from app.routing.types import Decision, Medium
from app.text.decider import text_decider
from app.text.events import AgentMessage, Typing, UserMessage
from app.text.messenger import Messenger
from app.text.reply import Reply
from app.text.responder import TextResponder
from app.timers import AsyncioTimers, Clock, Timers
from app.users.live import LiveUser, LiveUsers
from app.voice.decider import voice_decider
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


def build_app(
    *,
    db: SessionFactory,
    messenger: Messenger,
    model: Model,
    app_base_url: str,
    openrouter_key: str | None = None,
    jev_model: str = "typesafe/jev-1.13",
    timers: Timers | None = None,
    clock: Clock = utc_now,
) -> Actions:
    def new_live_user(phone: str) -> LiveUser:
        live = LiveUser(phone)
        reply = Reply(
            agent,
            phone=phone,
            actions=actions,  # defined below; only called after build_app returns
            messenger=messenger,
            model=model,
            app_base_url=app_base_url,
        )
        live.responders[Medium.TEXT] = TextResponder(
            runner=reply,
            decider=text_decider(openrouter_key=openrouter_key, jev_model=jev_model),
            timers=timers or AsyncioTimers(),
            clock=clock,
        )
        live.responders[Medium.VOICE] = VoiceResponder(
            sink=live,
            notes=call_note,
            decider=voice_decider(openrouter_key=openrouter_key, jev_model=jev_model),
            clock=clock,
        )
        return live

    actions = Actions(db, LiveUsers(new_live_user), payloads=PAYLOADS, clock=clock)
    return actions
