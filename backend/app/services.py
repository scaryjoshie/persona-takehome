"""What routes need, put on the app's state at startup and read with `Depends`.

@router.get("/api/x")
async def x(svc: ServicesDep) -> ...: svc.pipeline...
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, WebSocket
from starlette.requests import HTTPConnection

from app.google.accounts import Google
from app.pipeline import Pipeline
from app.voice.responder import VoiceResponder
from app.web.sockets import Sockets

CallRunner = Callable[[WebSocket, str], Awaitable[None]]
Transcribe = Callable[[bytes, str, str], Awaitable[str | None]]  # (audio, filename, type) → text


@dataclass(frozen=True)
class Services:
    pipeline: Pipeline
    voice: VoiceResponder
    sockets: Sockets
    run_call: CallRunner  # runs one voice call on an accepted audio socket
    transcribe: Transcribe  # voice messages
    voice_notes_dir: Path
    app_base_url: str
    google: Google  # connected Google accounts (real or demo)


def _services(connection: HTTPConnection) -> Services:
    return connection.app.state.services


ServicesDep = Annotated[Services, Depends(_services)]
