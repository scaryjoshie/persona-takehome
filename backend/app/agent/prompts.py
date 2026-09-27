"""Prompt fragments, written as markdown files next to this module and loaded once."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


def read_md(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def parse(markdown: str) -> dict[str, str]:
    """Split an objective file into its sections: "# label" is "title", the untitled top is
    the key ""."""
    sections: dict[str, list[str]] = {"": []}
    key = ""
    for line in markdown.splitlines():
        if line.startswith("# ") and key == "" and "title" not in sections:
            sections["title"] = [line[2:].strip()]
        elif line.startswith("## "):
            key = line[3:].strip().lower()
            sections[key] = []
        else:
            sections[key].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


PERSONA = read_md("persona")  # who the agent is and how it talks; every channel
ONBOARDING = read_md("onboarding")  # the job right now; every channel
TEXT = read_md("text")  # the text channel's format
CALL = read_md("call")  # the voice on a call
CALL_AGENT = read_md("call_agent")  # the call agent during a call
VOICE_BACKEND = read_md("voice_backend")  # the voice's own delegation backend
JOBS = read_md("jobs")  # handing work to background tasks; once onboarding is done
JOB = read_md("job")  # a background task itself

# The first message, sent as written unless their first text says more than hi (text/reply.py).
OPENER = parse(read_md("opener"))

# One file per onboarding objective, split into sections (see app/agent/objectives.py).
OBJECTIVE_TEXTS = {
    name: parse(read_md(f"objectives/{name}"))
    for name in ("agent_name", "user_name", "google", "help_need", "wrap_up")
}
