"""Onboarding as objectives: an ordered list, worked through one at a time. The active one is
the first that's neither done nor set aside. Only it is shown to the agent, in full; what's
already done shows up as facts, and nothing ahead of it shows at all (listed, it got recited
as an agenda).

Each objective is when it counts as done (here) plus its words, in prompts/objectives/<name>.md:

    The goal, and how to go about it. Always shown.
    ## by text              shown only by text
    ## on a call            shown only on a call
    ## example              a line for it, written to fit the conversation
    ## example: on a call   the same, on a call

Goals, not commands ("what they'd like to be called", not "ask their name"): a picture of where
things stand that reaches the voice a moment late can't make it ask for something it just got.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.users.user import Medium

DECIDED = (GmailPhase.CONNECTED, GmailPhase.SKIPPED, GmailPhase.DISCONNECTED)


@dataclass(frozen=True)
class Objective:
    name: str
    done: Callable[[Slots, bool], bool]  # (slots, first reply) -> done

    def handled(self, slots: Slots, first_reply: bool) -> bool:
        """Done, or set aside because they'd rather not."""
        return self.done(slots, first_reply) or self.name in slots.set_aside

    def brief(self, medium: Medium, slots: Slots) -> str:
        """Its words for this channel: the goal, then the example line if it has one."""
        sections = OBJECTIVE_TEXTS[self.name]
        channel = "on a call" if medium is Medium.VOICE else "by text"
        example = sections.get(f"example: {channel}") or sections.get("example")
        if example:
            line = example.removeprefix("- ").replace("[name]", slots.user_name or "(their name)")
            example = f"For example: {line}"
        return "\n\n".join(t for t in (sections[""], sections.get(channel), example) if t)


OBJECTIVES: tuple[Objective, ...] = (
    Objective("intro", done=lambda slots, first_reply: not first_reply),
    # Nice to have: someone who led with a real task and gave their own name isn't held up.
    Objective(
        "agent_name",
        done=lambda slots, _: (
            slots.agent_name is not None
            or (slots.help_need is not None and slots.user_name is not None)
        ),
    ),
    Objective("user_name", done=lambda slots, _: slots.user_name is not None),
    # Before what they need: connected, it can see what's going on and suggest.
    Objective("google", done=lambda slots, _: slots.gmail in DECIDED),
    Objective("help_need", done=lambda slots, _: slots.help_need is not None),
    Objective("wrap_up", done=lambda slots, _: slots.graduated),
)


def active(slots: Slots, *, first_reply: bool = False) -> Objective | None:
    return next((o for o in OBJECTIVES if not o.handled(slots, first_reply)), None)


def brief(slots: Slots, medium: Medium, *, first_reply: bool = False) -> str:
    """The active objective for the prompt, or "" once onboarding is done."""
    objective = active(slots, first_reply=first_reply)
    return f"## Your objective\n\n{objective.brief(medium, slots)}" if objective else ""
