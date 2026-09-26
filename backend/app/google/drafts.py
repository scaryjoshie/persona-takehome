"""Email drafts as things in the conversation, not words about them.

A draft is an `email_draft` event: what it says, its Gmail draft id, and whether it's been
sent. Each edit posts a new version under the same `ref`, and the phone shows the latest as
a card (missing fields visible). Sending always sends the stored Gmail draft by id, never
text the model retyped, and goes through one function whether it came from the card's Send
button or from the agent.
"""

from __future__ import annotations

import uuid

from app.events.event import Event
from app.events.payload import Channel, Origin
from app.google.accounts import Account
from app.google.events import EmailDraft
from app.pipeline import Pipeline
from app.text.events import UserMessage


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
    """Create a draft, or update one (same ref, same Gmail draft), and show it as a card."""
    previous = latest(await pipeline.history(phone), ref) if ref else None
    if previous and previous[1].status == "sent":
        raise ValueError(f"email {ref} was already sent; start a new draft")
    gmail_id = previous[1].gmail_id if previous else ""
    gmail_id = await account.draft(to=to, subject=subject, body=body, draft_id=gmail_id or None)
    draft = EmailDraft(
        ref=ref or uuid.uuid4().hex[:8], to=to, subject=subject, body=body, gmail_id=gmail_id
    )
    await pipeline.submit(phone, Origin.TEXT_AGENT, Channel.TEXT, draft)
    return draft


async def send(
    pipeline: Pipeline, phone: str, account: Account, ref: str, *, need_reply: bool
) -> EmailDraft:
    """Send the stored draft. `need_reply`: the agent may only send once they've answered
    since this version was shown (the Send button is itself the yes)."""
    events = await pipeline.history(phone)
    found = latest(events, ref)
    if found is None:
        raise ValueError(f"no draft {ref}")
    seq, draft = found
    if draft.status == "sent":
        raise ValueError(f"email {ref} was already sent")
    if draft.missing:
        raise ValueError(f"draft {ref} is missing {', '.join(draft.missing)}")
    if need_reply and not any(e.seq > seq and isinstance(e.payload, UserMessage) for e in events):
        raise ValueError(f"they haven't answered since you showed draft {ref}; ask first")
    await account.send(draft.gmail_id)
    sent = draft.model_copy(update={"status": "sent"})
    await pipeline.submit(phone, Origin.TEXT_AGENT, Channel.TEXT, sent)
    return sent
