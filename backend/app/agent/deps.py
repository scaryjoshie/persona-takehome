"""What agent runs need. `AgentEnv` is built once at startup; `Deps` is one run's view."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.models import Model

from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.messenger import Messenger
from app.users.user import Medium, User


@dataclass(frozen=True)
class AgentEnv:
    pipeline: Pipeline
    messenger: Messenger
    model: Model  # the text model; also runs the call's back-office listener
    app_base_url: str  # for links the agent sends, like the Gmail link

    def deps(self, user: User, medium: Medium) -> Deps:
        return Deps(user=user, medium=medium, env=self)


@dataclass(frozen=True)
class Deps:
    user: User  # a snapshot taken when the run started
    medium: Medium
    env: AgentEnv

    @property
    def pipeline(self) -> Pipeline:
        return self.env.pipeline

    @property
    def messenger(self) -> Messenger:
        return self.env.messenger

    @property
    def phone(self) -> str:
        return self.user.phone

    @property
    def origin(self) -> Origin:
        return Origin.VOICE_AGENT if self.medium is Medium.VOICE else Origin.TEXT_AGENT

    @property
    def channel(self) -> Channel:
        return Channel.VOICE if self.medium is Medium.VOICE else Channel.TEXT
