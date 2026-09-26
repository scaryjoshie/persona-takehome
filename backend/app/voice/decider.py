"""How the voice side judges an event that arrives while the agent is speaking on a call.

On a call the verbs mean: interrupt = hand the event to the speaking model as something
to say now; absorb = hand it over as a silent note; defer = hold it until the agent
finishes its sentence, then hand it over to say.
"""

from __future__ import annotations

from app.routing.deciders import Decider, DefaultDecider, JevDecider
from app.routing.types import Verb

QUESTION = (
    "A new event arrived while the assistant is responding on a phone call. "
    "How should the assistant handle it?"
)

# Measured 2026-09-26: this wording separates "user typing after the agent asked a question"
# (interrupt) from "user typing while the agent explains" (defer); looser variants did not.
CRITERIA = {
    Verb.INTERRUPT: (
        "The event is a reply to something the assistant is waiting on, or needs a response "
        "right now. Address it immediately."
    ),
    Verb.ABSORB: "The event is background information the assistant should know but not remark on.",
    Verb.DEFER: (
        "The event deserves a response, but the assistant is mid-response on something else "
        "and should finish first."
    ),
}

DEFAULTS = {
    "typing": Verb.ABSORB,  # never cut the speaker off for a typing signal
    "gmail": Verb.DEFER,  # finish the sentence, then react
}


def voice_decider(*, openrouter_key: str | None, jev_model: str) -> Decider:
    fallback = DefaultDecider(DEFAULTS)
    if openrouter_key is None:
        return fallback
    return JevDecider(
        api_key=openrouter_key,
        model=jev_model,
        question=QUESTION,
        criteria=CRITERIA,
        fallback=fallback,
    )
