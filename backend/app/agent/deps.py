"""What the agent's tools can reach during one run."""

from __future__ import annotations

from dataclasses import dataclass

from app.events.payload import Channel, Origin
from app.pipeline import Pipeline
from app.text.messenger import Messenger
from app.users.user import Medium, User


@dataclass
class Deps:
    user: User  # a snapshot taken when the run started
    pipeline: Pipeline
    messenger: Messenger
    medium: Medium
    app_base_url: str

    @property
    def phone(self) -> str:
        return self.user.phone

    @property
    def origin(self) -> Origin:
        return Origin.VOICE_AGENT if self.medium is Medium.VOICE else Origin.TEXT_AGENT

    @property
    def channel(self) -> Channel:
        return Channel.VOICE if self.medium is Medium.VOICE else Channel.TEXT
