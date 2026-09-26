"""Simulated users against the running server, then a judge flags unnatural lines.

    uv run uvicorn app.main:app --port 8765 &      # the app under test
    uv run python -m evals.simulate [persona ...]  # all personas if none given

A cheap model plays each persona over the same WebSocket the browser uses (so debounce,
Jev, and tools are all real). A second model reads each transcript as a first-time user
would and quotes lines a person wouldn't say. Report: evals/out/<timestamp>.md
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets
from pydantic import BaseModel
from pydantic_ai import Agent

from app.agent.model import text_model
from app.settings import get_settings

URL = "ws://localhost:8765/ws?phone={phone}"
MAX_TURNS = 9
QUIET = 8.0  # seconds without a new bubble = the agent is done replying

PERSONAS = {
    "skeptic": "You got a text from an unknown number saying it's your new assistant. "
    "You're curious but suspicious; you ask what this is and why it wants things.",
    "terse": "You're busy. You reply in 1-4 words, lowercase, no punctuation. You'll "
    "go along with reasonable asks but won't write much.",
    "joker": "You like messing with bots: joke names, silly questions, a tangent or two, "
    "but you do eventually play along.",
    "task_first": "You have a real need (your landlord keeps not replying about a broken "
    "heater) and you want help with that right away, not a setup process.",
    "privacy": "You're worried about giving an app access to your email. You ask what it "
    "can see, and you decline Gmail unless the answer is really reassuring.",
    "non_techy": "You're 68, not very technical. Your grandkid set this up. You write full "
    "sentences with capitals, and you're unsure what an 'agent' or 'Gmail link' means.",
}


class Next(BaseModel):
    message: str  # what you text next; empty if you'd stop replying here
    done: bool


class Verdict(BaseModel):
    score: int  # 1-10: how natural and helpful the assistant felt to a first-time user
    awkward: list[str]  # assistant lines (quoted exactly) a person wouldn't say, with why
    summary: str


def user_agent(persona: str) -> Agent[None, Next]:
    return Agent(
        output_type=Next,
        instructions=(
            f"You are role-playing a person texting with a new AI assistant. {persona} "
            "Reply with only your next text message, in character, realistic for iMessage. "
            "If you'd naturally stop replying, set done=true. If the assistant tries to call "
            "you, say you'd rather text (this simulation is text only)."
        ),
    )


judge: Agent[None, Verdict] = Agent(
    output_type=Verdict,
    instructions=(
        "You review an onboarding conversation between a new user and an AI personal "
        "assistant that texts like a friend. Read it as the user, meeting it for the first "
        "time. Flag every assistant line that a real, socially skilled person would not say: "
        "cryptic references ('they want me to...'), stock phrases, salesy or needy lines, "
        "repeating itself, asking two things at once, over-long messages, robotic "
        "confirmations. Quote each flagged line exactly, then a short reason. Be strict."
    ),
)


async def converse(name: str, phone: str) -> list[tuple[str, str]]:
    model = text_model(get_settings())
    player = user_agent(PERSONAS[name])
    transcript: list[tuple[str, str]] = []
    async with websockets.connect(URL.format(phone=phone)) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "reset"}))
        await ws.recv()
        for _ in range(MAX_TURNS):
            history = "\n".join(f"{who}: {text}" for who, text in transcript) or "(nothing yet)"
            nxt = (await player.run(f"Conversation so far:\n{history}", model=model)).output
            if nxt.done or not nxt.message.strip():
                break
            transcript.append(("user", nxt.message))
            await ws.send(json.dumps({"type": "message", "text": nxt.message}))
            got = False
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), QUIET if got else 40)
                except TimeoutError:
                    break
                m = json.loads(raw)
                if m["type"] == "event" and m["event"]["payload"]["kind"] == "agent_message":
                    transcript.append(("assistant", m["event"]["payload"]["text"]))
                    got = True
                if m["type"] == "call":
                    if m["call"]["phase"] == "ringing":
                        await ws.send(json.dumps({"type": "call", "action": "decline"}))
    return transcript


async def main(names: list[str]) -> None:
    model = text_model(get_settings())
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    report = [f"# Simulated onboarding — {time.strftime('%Y-%m-%d %H:%M')}\n"]
    results = await asyncio.gather(*(converse(n, f"1777000{i:04d}") for i, n in enumerate(names)))
    for name, transcript in zip(names, results, strict=True):
        text = "\n".join(f"{who}: {line}" for who, line in transcript)
        verdict = (await judge.run(text, model=model)).output
        report.append(f"## {name} — {verdict.score}/10\n\n{verdict.summary}\n")
        report.append("**Flagged:**\n" + "\n".join(f"- {a}" for a in verdict.awkward) + "\n")
        report.append(
            "<details><summary>transcript</summary>\n\n```\n" + text + "\n```\n</details>\n"
        )
        print(f"{name:10} {verdict.score}/10  flagged {len(verdict.awkward)}")
    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text("\n".join(report))
    print(f"report: {path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(PERSONAS)))
