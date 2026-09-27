"""What the voice means, judged by Jev (a fast classifier with probabilities), not keywords:
"on it" commits to something and "i'll text you later" doesn't, which no pattern gets right.

Without Jev these return nothing: the back office still runs after every finished turn, just
without the early start, and slips go uncorrected."""

from __future__ import annotations

from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.jev import Jev

SURE = 0.6  # Jev's probability needed to act on its answer

COMMIT = (
    "The assistant is mid-sentence on a phone call. Going by its words so far, is it saying "
    "it's doing something for them right now or next (texting them a link or anything else, "
    "drafting or sending an email, adding a calendar event)? Talking about something it could "
    "do, asking whether to, or doing it later is not."
)


async def commits(jev: Jev | None, conversation: list[str], saying: str) -> bool:
    """The voice, still talking, has just said it's on something."""
    if jev is None:
        return False
    p = await jev.yes_probability(COMMIT, {"conversation": conversation, "saying_now": saying})
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
