"""Entry point.

- assemble: plugs the pieces together (pipeline, the text and voice media).
- from_settings: builds everything from backend/.env; used by the web app and the CLI.
- app: the FastAPI app. Run with `uv run uvicorn app.main:app --reload`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic_ai.models import Model
from pydantic_ai.realtime.openai_live import OpenAILiveModel

from app.agent.call_notes import call_note
from app.agent.deps import AgentEnv
from app.agent.model import live_model, text_model
from app.database import SessionFactory, create_schema, make_engine, make_sessions, utc_now
from app.jev import Jev
from app.payloads import PAYLOADS
from app.pipeline import Pipeline
from app.previews.routes import make_router as make_preview_router
from app.settings import Settings, get_settings
from app.text.messenger import Messenger
from app.text.reply import Replier
from app.text.responder import TextResponder
from app.timers import AsyncioTimers, Clock, Timers
from app.users.user import Medium
from app.voice.call import run_call
from app.voice.responder import VoiceResponder
from app.web.routes import make_router
from app.web.sockets import Sockets, WebMessenger


@dataclass
class App:
    env: AgentEnv
    voice: VoiceResponder

    @property
    def pipeline(self) -> Pipeline:
        return self.env.pipeline


def assemble(
    *,
    db: SessionFactory,
    messenger: Messenger,
    model: Model,
    app_base_url: str,
    jev: Jev | None = None,
    timers: Timers | None = None,
    clock: Clock = utc_now,
) -> App:
    pipeline = Pipeline(db, payloads=PAYLOADS, clock=clock, timers=timers or AsyncioTimers())
    env = AgentEnv(pipeline=pipeline, messenger=messenger, model=model, app_base_url=app_base_url)
    voice = VoiceResponder(notes=call_note, jev=jev)
    pipeline.responders[Medium.TEXT] = TextResponder(Replier(env), jev=jev)
    pipeline.responders[Medium.VOICE] = voice
    return App(env=env, voice=voice)


def from_settings(settings: Settings, messenger: Messenger) -> tuple[App, OpenAILiveModel]:
    """Everything the process needs, built once. Returns the app and the voice model."""
    jev = None
    if settings.openrouter_api_key:
        jev = Jev(api_key=settings.openrouter_api_key.get_secret_value(), model=settings.jev_model)
    built = assemble(
        db=make_sessions(make_engine(settings.database_url)),
        messenger=messenger,
        model=text_model(settings),
        app_base_url=settings.app_base_url,
        jev=jev,
    )
    return built, live_model(settings)


# ---- the web app -------------------------------------------------------------------------


def create_app() -> FastAPI:
    settings = get_settings()
    sockets = Sockets()
    built, voice_model = from_settings(settings, WebMessenger(sockets))

    async def start_call(websocket: WebSocket, phone: str) -> None:
        await run_call(
            websocket,
            phone=phone,
            env=built.env,
            live_model=voice_model,
            voice=built.voice,
            push=sockets.push,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await create_schema(make_engine(settings.database_url))
        yield

    web = FastAPI(lifespan=lifespan)
    web.include_router(make_router(built.pipeline, sockets, start_call, built.voice))
    web.include_router(make_preview_router())
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        web.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return web


app = create_app()
