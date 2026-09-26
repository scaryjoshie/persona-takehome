"""A draft email as a picture, the kind of image a texting assistant can send: what the
draft says, with anything still missing marked, so they can check it before saying yes."""

from __future__ import annotations

import textwrap
from html import escape

from app.google.events import EmailDraft

WIDTH = 720
PAD = 36
LINE = 30  # body line height
WRAP = 52  # characters per body line
MAX_LINES = 18
FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif"


def render(draft: EmailDraft) -> str:
    lines = _wrap(draft.body) if draft.body.strip() else []
    body_top = 250
    height = body_top + max(len(lines), 1) * LINE + PAD + (54 if draft.missing else 0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" font-family="{FONT}">',
        f'<rect width="{WIDTH}" height="{height}" rx="28" fill="#ffffff"/>',
        f'<text x="{PAD}" y="62" font-size="30" font-weight="700" fill="#111">New Message</text>',
        _field("To", draft.to, 118),
        _field("Subject", draft.subject, 180),
    ]
    if lines:
        for i, line in enumerate(lines):
            y = body_top + 8 + i * LINE
            parts.append(
                f'<text x="{PAD}" y="{y}" font-size="22" fill="#222">{escape(line)}</text>'
            )
    else:
        parts.append(_missing(PAD, body_top + 8, "message missing"))
    if draft.missing:
        y = height - 32
        parts.append(
            f'<text x="{PAD}" y="{y}" font-size="20" fill="#c2410c">Still needs: '
            f"{escape(', '.join(_label(m) for m in draft.missing))}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _field(label: str, value: str, y: int) -> str:
    rule = f'<line x1="{PAD}" y1="{y + 26}" x2="{WIDTH - PAD}" y2="{y + 26}" stroke="#e5e5ea"/>'
    name = f'<text x="{PAD}" y="{y}" font-size="22" fill="#8e8e93">{label}:</text>'
    x = PAD + (54 if label == "To" else 98)
    if not value.strip():
        return name + _missing(x, y) + rule
    shown = value if len(value) <= 44 else value[:43] + "…"
    return name + f'<text x="{x}" y="{y}" font-size="22" fill="#111">{escape(shown)}</text>' + rule


def _missing(x: int, y: int, what: str = "missing") -> str:
    style = 'font-size="22" font-style="italic" fill="#c7c7cc"'
    return f'<text x="{x}" y="{y}" {style}>{what}</text>'


def _wrap(body: str) -> list[str]:
    lines: list[str] = []
    for paragraph in body.splitlines():
        lines += textwrap.wrap(paragraph, WRAP) or [""]
    if len(lines) > MAX_LINES:
        lines = [*lines[: MAX_LINES - 1], "…"]
    return lines


def _label(field: str) -> str:
    return {"to": "who it's to", "subject": "a subject", "body": "the message"}[field]
