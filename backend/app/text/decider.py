"""How the text side judges an event that arrives while a reply is being written.

On text the verbs mean: interrupt = scrap the reply in progress and write a new one;
absorb = keep writing (a typing signal only stretches the wait before the next reply);
defer = let the reply go out, then answer this next.
"""

from __future__ import annotations

from app.routing.deciders import Decider, DefaultDecider, JevDecider
from app.routing.types import Verb

QUESTION = "The assistant is writing a text reply when this event arrives. What should it do?"

CRITERIA = {
    Verb.INTERRUPT: (
        "Stop writing the reply in progress and write a new one that takes this into account."
    ),
    Verb.ABSORB: (
        "Keep writing the reply in progress; this only changes how long to wait before "
        "the next one."
    ),
    Verb.DEFER: "Let the reply in progress go out as is, then answer this in the next reply.",
}

DEFAULTS = {
    "typing": Verb.ABSORB,  # a typing signal never cancels a reply by itself
    "gmail": Verb.DEFER,  # finish the reply, then react
}


def text_decider(*, openrouter_key: str | None, jev_model: str) -> Decider:
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
