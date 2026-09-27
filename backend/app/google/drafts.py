"""Email drafts as things in the conversation, not words about them.

A draft is an `email_draft` event: what it says, its Gmail draft id, and whether it's been
sent. Each version is texted to them as an image of the draft (preview.py), missing fields
marked, and each edit posts a new version under the same `ref`. Sending sends the stored
Gmail draft by id, never text the model retyped, and only after they've answered (by text,
or out loud on a call: the picture is in their texts either way).
"""

from __future__ import annotations

import uuid

from app.events.event import Event
from app.events.payload import Channel, Origin
from app.google.accounts import Account
from app.google.events import EmailDraft
from app.pipeline import Pipeline
from app.text.events import UserMessage
from app.voice.events import Speaker, VoiceUtterance


def _from_them(event: Event) -> bool:
    """Something they said: a text, or a turn on a call."""
    p = event.payload
    return isinstance(p, UserMessage) or (
        isinstance(p, VoiceUtterance) and p.speaker is Speaker.USER
    )


def latest(events: list[Event], ref: str) -> tuple[int, EmailDraft] | None:
    """The newest version of a draft and its seq."""
    found = None
    for event in events:
        if isinstance(event.payload, EmailDraft) and event.payload.ref == ref:
            found = (event.seq, event.payload)
    return found


async def save(
    pipeline: Pipeline,
    phone: str,
    account: Account,
    *,
    ref: str | None,
    to: str,
    subject: str,
    body: str,
) -> EmailDraft:
    """Create a draft, or update one (same ref, same Gmail draft), and text them a picture of it.
    On an update, a field left blank keeps what the draft had: fixing the address alone must
    not wipe the message (it once sent them a picture of an empty draft)."""
    previous = latest(await pipeline.history(phone), ref) if ref else None
    if previous and previous[1].status == "sent":
        raise ValueError(f"email {ref} was already sent; start a new draft")
    if previous:
        was = previous[1]
        to, subject, body = to or was.to, subject or was.subject, body or was.body
    gmail_id = previous[1].gmail_id if previous else ""
    gmail_id = await account.draft(to=to, subject=subject, body=body, draft_id=gmail_id or None)
    draft = EmailDraft(
        ref=ref or uuid.uuid4().hex[:8], to=to, subject=subject, body=body, gmail_id=gmail_id
    )
    await pipeline.submit(phone, Origin.TEXT_AGENT, Channel.TEXT, draft)
    return draft


async def send(pipeline: Pipeline, phone: str, account: Account, ref: str) -> EmailDraft:
    """Send the stored draft, once they've answered since this version was shown."""
    events = await pipeline.history(phone)
    found = latest(events, ref)
    if found is None:
        raise ValueError(f"no draft {ref}")
    seq, draft = found
    if draft.status == "sent":
        raise ValueError(f"email {ref} was already sent")
    if draft.missing:
        raise ValueError(f"draft {ref} is missing {', '.join(draft.missing)}")
    if not any(e.seq > seq and _from_them(e) for e in events):
        raise ValueError(f"they haven't answered since you showed draft {ref}; ask first")
    await account.send(draft.gmail_id)
    sent = draft.model_copy(update={"status": "sent"})
    await pipeline.submit(phone, Origin.TEXT_AGENT, Channel.TEXT, sent)
    return sent
