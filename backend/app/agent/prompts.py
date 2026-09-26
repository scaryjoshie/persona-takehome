"""Prompt fragments, written as markdown files next to this module and loaded once."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


def read_md(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


PERSONA = read_md("persona")  # who the agent is and how it talks; every channel
ONBOARDING = read_md("onboarding")  # the job right now; every channel
TEXT = read_md("text")  # the text channel's format
CALL = read_md("call")  # the voice on a call
LISTENER = read_md("listener")  # the back office during a call
