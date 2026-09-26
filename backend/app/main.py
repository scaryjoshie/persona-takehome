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

from app.agent.deps import AgentEnv, Messenger
from app.agent.model import live_model, text_model
from app.database import SessionFactory, create_schema, make_engine, make_sessions, utc_now
from app.google import routes as google_routes
from app.google.accounts import Google
from app.jev import Jev
from app.pipeline import Pipeline
from app.previews import routes as preview_routes
from app.services import Services
from app.settings import Settings, get_settings
from app.text import voice_notes as voice_note_routes
from app.text.reply import Replier
from app.text.responder import TextResponder
from app.text.voice_notes import Transcriber
from app.timers import AsyncioTimers, Clock, Timers
from app.users.user import Medium
from app.voice import routes as voice_routes
from app.voice.call import run_call
from app.voice.responder import VoiceResponder
from app.web import routes as web_routes
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
    google: Google | None = None,
) -> App:
    pipeline = Pipeline(db, clock=clock, timers=timers or AsyncioTimers())
    voice = VoiceResponder(jev=jev)
    env = AgentEnv(
        pipeline=pipeline,
        messenger=messenger,
        model=model,
        app_base_url=app_base_url,
        hang_up=voice.hang_up,
        google=google or Google(db),  # unconfigured: the Google link says so
    )
    pipeline.responders[Medium.TEXT] = TextResponder(Replier(env), jev=jev)
    pipeline.responders[Medium.VOICE] = voice
    return App(env=env, voice=voice)


def from_settings(settings: Settings, messenger: Messenger) -> tuple[App, OpenAILiveModel]:
    """Everything the process needs, built once. Returns the app and the voice model."""
    jev = None
    if settings.openrouter_api_key:
        jev = Jev(api_key=settings.openrouter_api_key.get_secret_value(), model=settings.jev_model)
    db = make_sessions(make_engine(settings.database_url))
    built = assemble(
        db=db,
        messenger=messenger,
        model=text_model(settings),
        app_base_url=settings.app_base_url,
        jev=jev,
        google=Google(
            db,
            creds=(settings.google_client_id, settings.google_client_secret.get_secret_value())
            if settings.google_client_id and settings.google_client_secret
            else None,
            key=settings.credentials_key.get_secret_value() if settings.credentials_key else None,
            tz=settings.timezone,
        ),
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
    voice_notes_dir = Path(settings.data_dir) / "voice_notes"
    voice_notes_dir.mkdir(parents=True, exist_ok=True)
    assert settings.openai_api_key is not None
    google = built.env.google
    assert google is not None  # assemble always sets one
    web.state.services = Services(
        pipeline=built.pipeline,
        voice=built.voice,
        sockets=sockets,
        run_call=start_call,
        transcribe=Transcriber(
            api_key=settings.openai_api_key.get_secret_value(), model=settings.transcribe_model
        ),
        voice_notes_dir=voice_notes_dir,
        app_base_url=settings.app_base_url,
        google=google,
    )
    for module in (web_routes, voice_routes, voice_note_routes, preview_routes, google_routes):
        web.include_router(module.router)
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        web.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return web


app = create_app()
