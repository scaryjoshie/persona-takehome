"""The texting interface the agent uses. Web now; iMessage or Twilio later."""

from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    async def send(self, phone: str, text: str) -> None: ...
    async def set_typing(self, phone: str, active: bool) -> None: ...
