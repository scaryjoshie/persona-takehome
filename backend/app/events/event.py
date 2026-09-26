from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, SerializeAsAny

from app.events.payload import Channel, Origin, Payload


class Event(BaseModel):
    """One row in a user's log. `seq` is 0 for transient (unpersisted) events."""

    model_config = ConfigDict(frozen=True)

    seq: int
    ts: datetime
    origin: Origin
    channel: Channel
    payload: SerializeAsAny[Payload]

    @property
    def kind(self) -> str:
        return self.payload.kind_name
