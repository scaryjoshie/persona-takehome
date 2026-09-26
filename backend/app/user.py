"""One user's state (slots, call, floor) over their store. Sections read and write
through this; the store only persists it as a blob."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.agent.types import SlotChanged, Slots
from app.calls.types import CallState
from app.events.base import Channel, Origin
from app.events.store import Store
from app.routing.types import Medium


class UserState(BaseModel):
    model_config = ConfigDict(frozen=True)

    slots: Slots = Slots()
    call: CallState = CallState()
    floor: Medium = Medium.TEXT


class User:
    def __init__(self, store: Store, state: UserState | None = None) -> None:
        self.store = store
        self.state = state or UserState()

    @property
    def phone(self) -> str:
        return self.store.phone

    @property
    def slots(self) -> Slots:
        return self.state.slots

    @property
    def call(self) -> CallState:
        return self.state.call

    @property
    def floor(self) -> Medium:
        return self.state.floor

    async def set_slot(self, slot: str, value: Any, *, origin: Origin, channel: Channel) -> bool:
        """Idempotent. Logs a slot_changed event when the value actually changes."""
        old = getattr(self.slots, slot)
        if old == value:
            return False
        await self._update(slots=self.slots.model_copy(update={slot: value}))
        await self.store.append(origin, channel, SlotChanged(slot=slot, old=old, new=value))
        return True

    async def set_call(self, call: CallState) -> None:
        floor = Medium.VOICE if call.phase == "connected" else Medium.TEXT
        await self._update(call=call, floor=floor)

    async def set_floor(self, floor: Medium) -> None:
        await self._update(floor=floor)

    async def _update(self, **changes: Any) -> None:
        self.state = self.state.model_copy(update=changes)
        await self.store.save_state(self.state)
