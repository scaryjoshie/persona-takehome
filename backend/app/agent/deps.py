"""What agent runs need. `AgentEnv` is built once at startup; `Deps` is one run's view."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic_ai.models import Model

from app.events.payload import Channel, Origin
from app.google.accounts import Google
from app.jev import Jev
from app.jobs.runner import Jobs
from app.pipeline import Pipeline
from app.users.user import Medium, User


class Messenger(Protocol):
    """How bubbles reach the user's phone: the web socket now; iMessage or Twilio later."""

    async def send(self, phone: str, text: str) -> None: ...
    async def set_typing(self, phone: str, active: bool) -> None: ...


@dataclass(frozen=True)
class AgentEnv:
    pipeline: Pipeline
    messenger: Messenger
    model: Model  # the text model; also runs the call agent
    app_base_url: str  # for links the agent sends, like the Gmail link
    # Ask the live call to hang up once the voice's goodbye has played. False: no live call.
    hang_up: Callable[[str], bool] | None = None
    google: Google | None = None  # connected Google accounts (email and calendar tools)
    jev: Jev | None = None  # fast yes/no and choice questions (on calls: what the voice means)
    jobs: Jobs | None = None  # background tasks, once onboarding is done

    def deps(
        self,
        user: User,
        medium: Medium,
        *,
        first_reply: bool = False,
        call_agent: bool = False,
        may_act: bool = True,
    ) -> Deps:
        return Deps(
            user=user,
            medium=medium,
            env=self,
            first_reply=first_reply,
            call_agent=call_agent,
            may_act=may_act,
        )


@dataclass
class Deps:
    user: User  # a snapshot taken when the run started
    medium: Medium
    env: AgentEnv
    # On a call two runs use the voice medium: the Live backend (the voice's own delegation)
    # and the call agent. Only the call agent records facts and sends things.
    call_agent: bool = False
    # Two keys for the call agent's actions: it runs after a voice turn, so the voice has
    # just said it's doing the thing. After their turn it only records facts.
    may_act: bool = True
    after_reply: list[str] = field(default_factory=lambda: [])  # texts to send after the bubbles
    first_reply: bool = False  # nothing has been said to them yet
    placed_call: bool = False  # set by start_call

    @property
    def pipeline(self) -> Pipeline:
        return self.env.pipeline

    @property
    def phone(self) -> str:
        return self.user.phone

    @property
    def origin(self) -> Origin:
        return Origin.VOICE_AGENT if self.medium is Medium.VOICE else Origin.TEXT_AGENT

    @property
    def channel(self) -> Channel:
        return Channel.VOICE if self.medium is Medium.VOICE else Channel.TEXT
