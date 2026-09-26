"""Which browser sockets are open for which phone, and pushing to them."""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import WebSocket
from pydantic import BaseModel

log = logging.getLogger(__name__)


class Sockets:
    def __init__(self) -> None:
        self._by_phone: dict[str, set[WebSocket]] = defaultdict(set)

    def add(self, phone: str, ws: WebSocket) -> None:
        self._by_phone[phone].add(ws)

    def remove(self, phone: str, ws: WebSocket) -> None:
        self._by_phone[phone].discard(ws)

    async def push(self, phone: str, message: BaseModel) -> None:
        text = message.model_dump_json()
        for ws in list(self._by_phone[phone]):
            try:
                await ws.send_text(text)
            except Exception:  # the socket closed between the check and the send
                log.info("%s: dropped a closed socket", phone)
                self.remove(phone, ws)


class WebMessenger:
    """Bubbles reach the browser as agent_message events, so `send` has nothing to do;
    only the typing indicator needs a message of its own."""

    def __init__(self, sockets: Sockets) -> None:
        self._sockets = sockets

    async def send(self, phone: str, text: str) -> None:
        return None

    async def set_typing(self, phone: str, active: bool) -> None:
        from app.web.protocol import TypingMessage

        await self._sockets.push(phone, TypingMessage(active=active))
