"""Prompt fragments, written as markdown files next to this module and loaded once."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


def read_md(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def parse(markdown: str) -> dict[str, str]:
    """Split an objective file into its sections; the untitled top is the key ""."""
    sections: dict[str, list[str]] = {"": []}
    key = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            key = line[3:].strip().lower()
            sections[key] = []
        else:
            sections[key].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


PERSONA = read_md("persona")  # who the agent is and how it talks; every channel
ONBOARDING = read_md("onboarding")  # the job right now; every channel
TEXT = read_md("text")  # the text channel's format
CALL = read_md("call")  # the voice on a call
LISTENER = read_md("listener")  # the back office during a call
VOICE_BACKEND = read_md("voice_backend")  # the voice's own delegation backend
JOBS = read_md("jobs")  # handing work to background tasks; once onboarding is done
JOB = read_md("job")  # a background task itself

# One file per onboarding objective, split into sections (see app/agent/objectives.py).
OBJECTIVE_TEXTS = {
    name: parse(read_md(f"objectives/{name}"))
    for name in (
        "opener",
        "agent_name",
        "contact",
        "user_name",
        "gmail",
        "help_need",
        "gmail_check",
        "wrap_up",
    )
}
