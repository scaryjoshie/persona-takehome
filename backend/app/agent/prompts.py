"""Prompt fragments as markdown, loaded once. A test fails on any orphaned file."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


def read_md(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


PERSONA = read_md("persona")
STYLE = read_md("style")
WORK = read_md("work")
TEXT_TAIL = read_md("text_tail")
SPEAKING = read_md("speaking")

ALL = {
    "persona": PERSONA,
    "style": STYLE,
    "work": WORK,
    "text_tail": TEXT_TAIL,
    "speaking": SPEAKING,
}
