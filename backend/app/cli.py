"""Talk to the onboarding agent from a terminal: `uv run python -m app.cli +15550001111`."""

from __future__ import annotations

import asyncio
import sys

from app.agent.model import agent_model
from app.database import create_schema, make_engine, make_sessions
from app.events.payload import Channel, Origin
from app.main import build_app, decider_from
from app.settings import get_settings
from app.text.events import UserMessage


class ConsoleMessenger:
    async def send(self, phone: str, text: str) -> None:
        print(f"\ragent: {text}")

    async def set_typing(self, phone: str, active: bool) -> None:
        if active:
            print("\r…", end="", flush=True)


async def main(phone: str) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    await create_schema(engine)
    app = build_app(
        db=make_sessions(engine),
        messenger=ConsoleMessenger(),
        model=agent_model(settings),
        app_base_url=settings.app_base_url,
        decider=decider_from(settings),
    )
    print(f"chatting as {phone}; ctrl-d to quit")
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        if line.strip():
            await app.actions.submit(
                phone, Origin.USER, Channel.TEXT, UserMessage(text=line.strip())
            )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "+15550001111"))
