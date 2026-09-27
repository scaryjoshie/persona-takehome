"""What the voice means, judged by Jev (a fast classifier with probabilities), not keywords:
"on it" commits to something and "i'll text you later" doesn't, which no pattern gets right.

Without Jev these return nothing: the call agent still runs after every finished turn, just
without the early start, and slips go uncorrected."""

from __future__ import annotations

from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.jev import Jev

SURE = 0.6  # Jev's probability needed to act on its answer

SIGN_OFF = 0.7  # measured on real goodbyes: 0.74-1.0; lines that weren't one never came close

SAYING = "The assistant is mid-sentence on a phone call. Going by its words so far:"
MOVES = {
    "doing": (
        "It's saying it's doing something for them right now or next: texting them a link or "
        "anything else, drafting or sending an email, adding a calendar event, looking "
        "something up."
    ),
    "goodbye": (
        "It's signing off: saying goodbye to end the call now, because things are wrapped up "
        "or they asked to go."
    ),
    "neither": (
        "Anything else, including talking about something it could do, asking whether to, "
        "doing it later, and a bye that doesn't end the call (\"i'll text you later\", "
        '"talk soon" before carrying on).'
    ),
}


async def saying(jev: Jev | None, conversation: list[str], words: str) -> str | None:
    """What the voice, still talking, has just done: "doing" (it's on something) or
    "goodbye" (it's ending the call); None for anything else, or when Jev isn't sure."""
    if jev is None:
        return None
    answer = await jev.choice(SAYING, MOVES, {"conversation": conversation, "saying_now": words})
    if answer is None or answer.choice == "neither":
        return None
    bar = SIGN_OFF if answer.choice == "goodbye" else SURE
    return answer.choice if answer.probabilities.get(answer.choice, 0.0) >= bar else None


ACCEPT = (
    "On a phone call, did the user's latest words say yes to something the assistant had just "
    "offered to do for them (text them a link, send or draft something, look something up)? "
    "Answering a question, agreeing with what it said, or okay to a piece of news is not."
)


async def accepts(jev: Jev | None, conversation: list[str]) -> bool:
    """They just said yes to something the voice offered: that's both keys, so act now rather
    than wait for the voice to also say it's on it."""
    if jev is None:
        return False
    p = await jev.yes_probability(ACCEPT, {"conversation": conversation})
    return p is not None and p >= SURE


SLIP = (
    "The assistant just said its latest line on a phone call. Did it offer to do, or ask for, "
    "something the facts say is already done?"
)
SLIPS = {
    "link": "It offered to send the Google link, and the facts say the link already went out.",
    "their_name": "It asked the user's name, and the facts say it already knows it.",
    "own_name": "It asked what to call it, and the facts say its name is settled.",
    "none": "None of these: nothing it offered or asked for is already done.",
}


async def slip(jev: Jev | None, slots: Slots, line: str) -> str | None:
    """A note telling the voice to correct itself, if its line re-offered something done."""
    if jev is None:
        return None
    link = {GmailPhase.LINK_SENT: "sent", GmailPhase.CONNECTED: "connected"}
    facts = {
        "google_link": link.get(slots.gmail, "not sent") if slots.gmail else "not sent",
        "user_name": slots.user_name or "unknown",
        "assistant_name": slots.agent_name or "not chosen yet",
    }
    answer = await jev.choice(SLIP, SLIPS, {"facts": facts, "latest_line": line})
    if answer is None or answer.choice == "none":
        return None
    if answer.probabilities.get(answer.choice, 0.0) < SURE:
        return None
    what = {
        "link": f"the Google link already went out ({facts['google_link']})",
        "their_name": f"you already know their name: {slots.user_name}",
        "own_name": f"your name is settled: {slots.agent_name}",
    }[answer.choice]
    return (
        f"You just offered or asked for something already done: {what}. Correct yourself in "
        "a few words, naturally."
    )
