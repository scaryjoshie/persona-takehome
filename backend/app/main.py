"""Entry point. Two jobs:

- build_app: wires the pieces for this process (pipeline, live users, responders).
- create_app / app: the FastAPI app. Run with `uv run uvicorn app.main:app --reload`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic_ai.models import Model

from app.agent.agent import agent
from app.agent.call_notes import call_note
from app.agent.model import agent_model
from app.database import SessionFactory, create_schema, make_engine, make_sessions, utc_now
from app.payloads import PAYLOADS
from app.pipeline import Pipeline
from app.routing.types import Medium
from app.settings import get_settings
from app.text.decider import text_decider
from app.text.messenger import Messenger
from app.text.reply import Reply
from app.text.responder import TextResponder
from app.timers import AsyncioTimers, Clock, Timers
from app.users.live import LiveUser, LiveUsers
from app.voice.call import VoiceConfig, run_call
from app.voice.decider import voice_decider
from app.voice.responder import VoiceResponder
from app.web.routes import make_router
from app.web.sockets import Sockets, WebMessenger


def build_app(
    *,
    db: SessionFactory,
    messenger: Messenger,
    model: Model,
    app_base_url: str,
    openrouter_key: str | None = None,
    jev_model: str = "typesafe/jev-1.13",
    timers: Timers | None = None,
    clock: Clock = utc_now,
) -> Pipeline:
    def new_live_user(phone: str) -> LiveUser:
        live = LiveUser(phone)
        reply = Reply(
            agent,
            phone=phone,
            pipeline=pipeline,  # defined below; only called after build_app returns
            messenger=messenger,
            model=model,
            app_base_url=app_base_url,
        )
        live.responders[Medium.TEXT] = TextResponder(
            runner=reply,
            decider=text_decider(openrouter_key=openrouter_key, jev_model=jev_model),
            timers=timers or AsyncioTimers(),
            clock=clock,
        )
        live.responders[Medium.VOICE] = VoiceResponder(
            sink=live,
            notes=call_note,
            decider=voice_decider(openrouter_key=openrouter_key, jev_model=jev_model),
            clock=clock,
        )
        return live

    pipeline = Pipeline(db, LiveUsers(new_live_user), payloads=PAYLOADS, clock=clock)
    return pipeline


# ---- the web app: `uv run uvicorn app.main:app --reload` --------------------------


def create_app() -> FastAPI:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sockets = Sockets()
    messenger = WebMessenger(sockets)
    pipeline = build_app(
        db=make_sessions(engine),
        messenger=messenger,
        model=agent_model(settings),
        app_base_url=settings.app_base_url,
        openrouter_key=(
            settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        ),
        jev_model=settings.jev_model,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await create_schema(engine)
        yield
        await engine.dispose()

    assert settings.openai_api_key is not None
    voice = VoiceConfig(
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
            pipeline=pipeline,
            agent=agent,
            messenger=messenger,
            push=sockets.push,
            config=voice,
        )

    web = FastAPI(lifespan=lifespan)
    web.include_router(make_router(pipeline, sockets, start_call))
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        web.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return web


app = create_app()
