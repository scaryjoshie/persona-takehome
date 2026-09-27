"""Objectives in a chain, shown to the agent like a quest log: where it is, what's done, what's
next. Only the current objective carries its words in full; the rest are one-line labels, so
it always knows the way on without being handed an agenda to recite.

A chain is an ordered list of objectives. An objective is when it counts as done (here) plus its
words, in prompts/objectives/<name>.md:

    # label                 its line in the log ("a name for you")
    What it's for and what matters. Shown when it's current.
    ## by text              shown only by text
    ## on a call            shown only on a call
    ## example              a line for it, written to fit the conversation
    ## example: on a call   the same, on a call

Onboarding is one chain; others (setting up a service, a first week) are just other lists.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    got: Callable[[Slots], str | None] = lambda slots: None  # what it came to, for the log

    def set_aside(self, slots: Slots) -> bool:
        """They'd rather not."""
        return self.name in slots.set_aside

    @property
    def label(self) -> str:
        return OBJECTIVE_TEXTS[self.name]["title"]

    def words(self, medium: Medium, slots: Slots) -> tuple[str, str | None]:
        """What it's for (with its part for this channel), and its example line."""
        sections = OBJECTIVE_TEXTS[self.name]
        channel = "on a call" if medium is Medium.VOICE else "by text"
        text = "\n\n".join(t for t in (sections[""], sections.get(channel)) if t)
        example = sections.get(f"example: {channel}") or sections.get("example")
        if example:
            example = example.removeprefix("- ").replace(
                "[name]", slots.user_name or "(their name)"
            )
        return text, example


@dataclass(frozen=True)
class Chain:
    title: str
    objectives: Sequence[Objective]

    def current(self, slots: Slots, *, first_reply: bool = False) -> Objective | None:
        return next(
            (
                o
                for o in self.objectives
                if not o.done(slots, first_reply) and not o.set_aside(slots)
            ),
            None,
        )

    def render(
        self, slots: Slots, medium: Medium, *, first_reply: bool = False, playbook: bool = False
    ) -> str:
        """Where you are in the chain, for the prompt; "" once every objective is behind.
        `playbook`: also how to handle each one still ahead, for when you get to it (the voice
        gets it once, at the start of a call, so a move never waits on its instructions)."""
        now = self.current(slots, first_reply=first_reply)
        if now is None:
            return ""
        log: list[str] = []
        for o in self.objectives:
            if o is now:
                log.append(f"▶ {o.label} (you're here)")
            elif o.set_aside(slots):
                log.append(f"– {o.label}: they'd rather not")
            elif o.done(slots, first_reply):
                got = o.got(slots)
                log.append(f"✓ {o.label}: {got}" if got else f"✓ {o.label}")
            else:
                log.append(f"· {o.label}")
        parts = [
            f"## {self.title}: where you are",
            "\n".join(log),
            f"Lead them through this, one at a time. Right now: {now.label}.",
            _how(now, medium, slots),
        ]
        ahead = self.objectives[self.objectives.index(now) + 1 :]
        if playbook and ahead:
            parts.append("### When you get to the next ones")
            parts += [f"**{o.label}**\n{_how(o, medium, slots)}" for o in ahead]
        return "\n\n".join(parts)

    def moved(self, before: str | None, slots: Slots) -> str | None:
        """A line for the voice when the chain moved on during a call, or None if it didn't.
        Said when it's free: it names what's done and where to go, and it's safe if the voice
        already got there on its own."""
        now = self.current(slots)
        if before is None or now is None or now.name == before:
            return None
        left = next(o for o in self.objectives if o.name == before)
        if left.set_aside(slots):
            behind = f"Leaving {left.label} for now; they'd rather not."
        else:
            got = left.got(slots)
            behind = f"Done: {left.label} ({got})." if got else f"Done: {left.label}."
        return f"{behind} If you haven't already, now's the time to move on to {now.label}."


def _how(o: Objective, medium: Medium, slots: Slots) -> str:
    """An objective's words, then its example line."""
    text, example = o.words(medium, slots)
    if not example:
        return text
    return (
        f"{text}\n\nWhen you ask this, use this line, fitted naturally to the moment and said in "
        "the language you're speaking with them. If something else needs handling first, "
        f"handle that first and bring this in after:\n{example}"
    )


def _google(slots: Slots) -> str | None:
    if slots.gmail is GmailPhase.CONNECTED:
        return f"connected ({slots.gmail_email})"
    return "they'd rather not" if slots.gmail is GmailPhase.SKIPPED else None


ONBOARDING = Chain(
    "Onboarding",
    (
        Objective("intro", done=lambda slots, first_reply: not first_reply),
        # Nice to have: someone who led with a real task and gave their own name isn't held up.
        Objective(
            "agent_name",
            done=lambda slots, _: (
                slots.agent_name is not None
                or (slots.help_need is not None and slots.user_name is not None)
            ),
            got=lambda slots: slots.agent_name,
        ),
        Objective(
            "user_name",
            done=lambda slots, _: slots.user_name is not None,
            got=lambda s: s.user_name,
        ),
        # Before what they need: connected, it can see what's going on and suggest.
        Objective("google", done=lambda slots, _: slots.gmail in DECIDED, got=_google),
        Objective(
            "help_need",
            done=lambda slots, _: slots.help_need is not None,
            got=lambda s: s.help_need,
        ),
        Objective("wrap_up", done=lambda slots, _: slots.graduated),
    ),
)
