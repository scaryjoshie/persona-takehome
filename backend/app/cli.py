"""Talk to the onboarding agent from a terminal: `uv run python -m app.cli +15550001111`."""

from __future__ import annotations

import asyncio
import sys

from app.actor import Actor
from app.agent.agent import agent
from app.agent.deps import Deps
from app.ai.models import agent_model
from app.channels.console import ConsoleChannel
from app.compose import build_actor, load
from app.events.base import Channel, Origin
from app.events.sql import make_engine
from app.routing.types import Medium
from app.settings import get_settings
from app.text.handler import TextHandler
from app.text.types import UserMessage


class NoVoice:
    async def send(self, text: str, *, speak: bool) -> None:
        print(f"\r[voice note{' (speak)' if speak else ''}]: {text}")


def make(phone: str) -> Actor:
    settings = get_settings()
    user = load(make_engine(settings.database_url), phone)
    holder: list[Actor] = []
    deps = Deps(
        user=user,
        submit=lambda o, c, p: holder[0].submit(o, c, p),
        channel=ConsoleChannel(user.slots.agent_name or "agent"),
        medium=Medium.TEXT,
        app_base_url=settings.app_base_url,
    )
    actor = build_actor(
        user,
        text_runner=TextHandler(agent, deps, model=agent_model(settings)),
        voice_sink=NoVoice(),
    )
    holder.append(actor)
    return actor


async def main(phone: str) -> None:
    actor = make(phone)
    actor.start()
    print(f"chatting as {phone}; ctrl-d to quit")
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        if line.strip():
            actor.submit(Origin.USER, Channel.TEXT, UserMessage(text=line.strip()))
    await actor.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "+15550001111"))
