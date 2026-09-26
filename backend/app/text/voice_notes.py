"""Voice messages from the user: upload, transcribe, then handled like any text.

POST /api/voice-note?phone=… (multipart: `audio`, optional `duration_ms`) stores the file
and returns its id at once; transcription runs in the background, then a VoiceNote event
goes through the pipeline. GET /api/voice-note/{audio_id} serves the audio back.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.events.payload import Channel, Origin
from app.services import ServicesDep
from app.text.events import VoiceNote
from app.web.routes import normalize

log = logging.getLogger(__name__)
router = APIRouter()

MAX_BYTES = 10 * 1024 * 1024
EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}
AUDIO_ID = re.compile(r"^[0-9a-f]{32}$")


class Uploaded(BaseModel):
    audio_id: str


class Transcriber:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def __call__(self, audio: bytes, filename: str, content_type: str) -> str | None:
        try:
            result = await self._client.audio.transcriptions.create(
                model=self._model, file=(filename, audio, content_type)
            )
            return result.text.strip() or None
        except Exception:
            log.exception("transcription failed for %s", filename)
            return None


@router.post("/api/voice-note")
async def upload(
    phone: str,
    audio: UploadFile,
    svc: ServicesDep,
    duration_ms: Annotated[int | None, Form()] = None,
) -> Uploaded:
    content_type = (audio.content_type or "").split(";")[0].strip()
    extension = EXTENSIONS.get(content_type)
    if extension is None:
        raise HTTPException(415, f"unsupported audio type {content_type!r}")
    data = await audio.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "voice message too large (10 MB max)")
    audio_id = uuid.uuid4().hex
    path = svc.voice_notes_dir / f"{audio_id}.{extension}"
    await asyncio.to_thread(path.write_bytes, data)

    async def transcribe_and_submit() -> None:
        transcript = await svc.transcribe(data, path.name, content_type)
        note = VoiceNote(audio_id=audio_id, duration_ms=duration_ms, transcript=transcript)
        await svc.pipeline.submit(normalize(phone), Origin.USER, Channel.TEXT, note)

    svc.pipeline.spawn(transcribe_and_submit())
    return Uploaded(audio_id=audio_id)


@router.get("/api/voice-note/{audio_id}")
async def play(audio_id: str, svc: ServicesDep) -> FileResponse:
    if not AUDIO_ID.match(audio_id):
        raise HTTPException(404)
    matches = list(svc.voice_notes_dir.glob(f"{audio_id}.*"))
    if not matches:
        raise HTTPException(404)
    extension = matches[0].suffix.lstrip(".")
    media_type = next((t for t, e in EXTENSIONS.items() if e == extension), "audio/webm")
    return FileResponse(matches[0], media_type=media_type)
