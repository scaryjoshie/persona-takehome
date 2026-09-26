"""Entry point. Two jobs:

- build_app: wires the pieces for this process (pipeline, live users, responders).
- create_app / app: the FastAPI app. Run with `uv run uvicorn app.main:app --reload`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic_ai.models import Model

from app.agent.agent import agent
from app.agent.call_notes import call_note
from app.agent.model import agent_model
from app.database import SessionFactory, create_schema, make_engine, make_sessions, utc_now
from app.jev import Jev
from app.payloads import PAYLOADS
from app.pipeline import Pipeline
from app.settings import get_settings
from app.text.messenger import Messenger
from app.text.reply import Replier
from app.text.responder import TextResponder
from app.timers import AsyncioTimers, Clock, Timers
from app.users.user import Medium
from app.voice.call import VoiceConfig, run_call
from app.voice.responder import VoiceResponder
from app.web.routes import make_router
from app.web.sockets import Sockets, WebMessenger


@dataclass
class App:
    pipeline: Pipeline
    voice: VoiceResponder


def build_app(
    *,
    db: SessionFactory,
    messenger: Messenger,
    model: Model,
    app_base_url: str,
    jev: Jev | None = None,
    timers: Timers | None = None,
    clock: Clock = utc_now,
) -> App:
    """The pipeline with its two media plugged in."""
    pipeline = Pipeline(db, payloads=PAYLOADS, clock=clock, timers=timers or AsyncioTimers())
    replier = Replier(
        agent, pipeline=pipeline, messenger=messenger, model=model, app_base_url=app_base_url
    )
    voice = VoiceResponder(notes=call_note, jev=jev)
    pipeline.responders[Medium.TEXT] = TextResponder(replier, jev=jev)
    pipeline.responders[Medium.VOICE] = voice
    return App(pipeline=pipeline, voice=voice)


# ---- the web app: `uv run uvicorn app.main:app --reload` --------------------------


def create_app() -> FastAPI:
    settings = get_settings()
    assert settings.openai_api_key is not None, "OPENAI_API_KEY is not set in backend/.env"
    engine = make_engine(settings.database_url)
    sockets = Sockets()
    messenger = WebMessenger(sockets)
    jev = (
        Jev(api_key=settings.openrouter_api_key.get_secret_value(), model=settings.jev_model)
        if settings.openrouter_api_key
        else None
    )
    built = build_app(
        db=make_sessions(engine),
        messenger=messenger,
        model=agent_model(settings),
        app_base_url=settings.app_base_url,
        jev=jev,
    )
    voice_config = VoiceConfig(
        listener_model=agent_model(settings),
        api_key=settings.openai_api_key.get_secret_value(),
        live_model=settings.openai_live_model,
        backend_model=settings.openai_live_backend_model,
        app_base_url=settings.app_base_url,
    )

    async def start_call(websocket: WebSocket, phone: str) -> None:
        await run_call(
            websocket,
            phone=phone,
            pipeline=built.pipeline,
            agent=agent,
            messenger=messenger,
            push=sockets.push,
            config=voice_config,
            voice=built.voice,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await create_schema(engine)
        yield
        await engine.dispose()

    web = FastAPI(lifespan=lifespan)
    web.include_router(make_router(built.pipeline, sockets, start_call, built.voice))
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        web.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return web


app = create_app()
