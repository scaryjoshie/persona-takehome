"""What the agent's tools can reach. One object per run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.channels.base import Channel
from app.events.base import Channel as EventChannel
from app.events.base import Origin, Payload
from app.routing.types import Medium
from app.user import User

Submit = Callable[[Origin, EventChannel, Payload], None]


@dataclass
class Deps:
    user: User
    submit: Submit
    channel: Channel
    medium: Medium
    app_base_url: str

    @property
    def origin(self) -> Origin:
        return Origin.VOICE_AGENT if self.medium is Medium.VOICE else Origin.TEXT_AGENT

    @property
    def event_channel(self) -> EventChannel:
        return EventChannel.VOICE if self.medium is Medium.VOICE else EventChannel.TEXT
