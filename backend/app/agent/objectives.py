"""Onboarding as an objective chain: a playbook of objectives, in order, and a pointer to the one
they're on. The playbook is static (the same text every turn); only the pointer moves, and it
moves on recorded facts (a name saved, Google connected). When it moves, the pipeline records
an ObjectiveMoved event, and a call says it out loud.

An objective's words live in prompts/objectives/<name>.md:

    # label                 its name in the pointer ("a name for you")
    What it's for and how to go about it.
    ## by text              only by text
    ## on a call            only on a call
    ## example              a line to use nearly as written, only where one is wanted
    ## example: on a call   the same, on a call
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from app.agent.events import ObjectiveMoved
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.users.user import Medium


@dataclass(frozen=True)
class Objective:
    name: str
    done: Callable[[Slots], bool]  # what's recorded when it's done
    got: Callable[[Slots], str | None] = lambda slots: None  # what it came to, for the pointer

    @property
    def label(self) -> str:
        return OBJECTIVE_TEXTS[self.name]["title"]

    def declined(self, slots: Slots) -> bool:
        """They'd rather not."""
        return self.name in slots.set_aside or (
            self.name == "google" and slots.gmail is GmailPhase.SKIPPED
        )

    def guide(self, medium: Medium) -> str:
        """How to go about it on this channel, with its example line if it has one."""
        sections = OBJECTIVE_TEXTS[self.name]
        channel = "on a call" if medium is Medium.VOICE else "by text"
        example = sections.get(f"example: {channel}") or sections.get("example")
        line = example.removeprefix("- ") if example else ""
        line = (
            f'When you ask for it, use this line, fitted naturally to the moment: "{line}"'
            if line
            else ""
        )
        return "\n\n".join(t for t in (sections[""], sections.get(channel), line) if t)


@dataclass(frozen=True)
class ObjectiveChain:
    title: str
    objectives: tuple[Objective, ...]

    def current(self, slots: Slots) -> Objective | None:
        return next(
            (o for o in self.objectives if not o.done(slots) and o.name not in slots.set_aside),
            None,
        )

    @cache  # noqa: B019 (chains are module constants)
    def playbook(self, medium: Medium) -> str:
        """Every objective in order, with how to go about it: the same text every turn."""
        steps = "\n\n".join(
            f"### {i}. {o.label}\n\n{o.guide(medium)}" for i, o in enumerate(self.objectives, 1)
        )
        return (
            f"# {self.title}\n\n"
            'Lead them through these in order, one at a time. "Where you are" says which one '
            "you're on; when it's done or they'd rather not, go on to the next.\n\n"
            f"{steps}"
        )

    def pointer(self, slots: Slots) -> str:
        """Where they are in it, in a line: what's behind them, and the one they're on."""
        now = self.current(slots)
        behind: list[str] = []
        for o in self.objectives:
            if o is now:
                break
            got = o.got(slots)
            if o.declined(slots):
                behind.append(f"{o.label} (they'd rather not)")
            else:
                behind.append(f"{o.label} ({got})" if got else o.label)
        done = f" Done: {'; '.join(behind)}." if behind else ""
        where = f"you're on {now.label}." if now else "it's all behind them."
        return f"Where you are in {self.title.lower()}: {where}{done}"

    def move(self, before: Slots, after: Slots) -> ObjectiveMoved | None:
        """The move a change of facts makes, if it makes one."""
        was, now = self.current(before), self.current(after)
        if was is None or was is now:
            return None
        return ObjectiveMoved(
            left=was.name, now=now.name if now else None, set_aside=was.declined(after)
        )

    def announcement(self, moved: ObjectiveMoved) -> str:
        """What a call hears when it moves: safe to hear if the voice already moved on."""
        label = {o.name: o.label for o in self.objectives}
        left = f"{label[moved.left]}: they'd rather not" if moved.set_aside else label[moved.left]
        if moved.now is None:
            return f"Done: {left}. That's everything for onboarding."
        return (
            f"Done: {left}. If you haven't already, now's the time to move on to "
            f"{label[moved.now]}."
        )


ONBOARDING = ObjectiveChain(
    "Onboarding",
    (
        # Nice to have: someone who led with a real task and gave their own name isn't held up.
        Objective(
            "agent_name",
            done=lambda s: (
                s.agent_name is not None or (s.help_need is not None and s.user_name is not None)
            ),
            got=lambda s: s.agent_name,
        ),
        Objective("user_name", done=lambda s: s.user_name is not None, got=lambda s: s.user_name),
        Objective("google", done=lambda s: s.google_decided, got=lambda s: s.gmail_email),
        Objective("help_need", done=lambda s: s.help_need is not None, got=lambda s: s.help_need),
        Objective("wrap_up", done=lambda s: s.graduated),
    ),
)
