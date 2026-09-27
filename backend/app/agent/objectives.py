"""Onboarding as an objective chain: a playbook of objectives, in order, and a pointer to the one
they're on. The playbook is static (the same text every turn); only the pointer moves, and it
moves on recorded facts (a name saved, Google connected). When it moves, the pipeline records
an ObjectiveMoved event, and a call says it out loud in the objective's own words.

An objective's words live in prompts/objectives/<name>.md:

    # label                 its name in the pointer ("a name for you")
    What it's for and how to go about it.
    ## by text              only by text
    ## on a call            only on a call
    ## example              a line to use nearly as written, only where one is wanted
    ## example: on a call   the same, on a call
    ## done                 what a call hears once it's done; {got} is what it came to
    ## skipped              the same when it was passed over (a nice-to-have they didn't need)
    ## declined             the same when they'd rather not
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from app.agent.events import How, ObjectiveMoved
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.users.user import Medium


def line(words: dict[str, str], medium: Medium) -> str | None:
    """The line to use nearly as written on this channel, if there is one."""
    channel = "on a call" if medium is Medium.VOICE else "by text"
    found = words.get(f"example: {channel}") or words.get("example")
    return found.removeprefix("- ") if found else None


def guide(words: dict[str, str], medium: Medium) -> str:
    """The words for this channel: what it's for, the channel's own notes, and its line."""
    channel = "on a call" if medium is Medium.VOICE else "by text"
    use = line(words, medium)
    use = f'Use this line, fitted naturally to the moment: "{use}"' if use else ""
    return "\n\n".join(t for t in (words[""], words.get(channel), use) if t)


@dataclass(frozen=True)
class Objective:
    name: str
    done: Callable[[Slots], bool]  # what's recorded when it's done
    got: Callable[[Slots], str | None] = lambda slots: None  # what it came to, for the pointer
    skipped: Callable[[Slots], bool] = lambda slots: False  # passed over: they didn't need it

    @property
    def words(self) -> dict[str, str]:
        return OBJECTIVE_TEXTS[self.name]

    @property
    def label(self) -> str:
        return self.words["title"]

    def declined(self, slots: Slots) -> bool:
        """They'd rather not."""
        return self.name in slots.set_aside or (
            self.name == "google" and slots.gmail is GmailPhase.SKIPPED
        )

    def behind(self, slots: Slots) -> How | None:
        """How it's behind them, if it is."""
        if self.done(slots):
            return "done"
        if self.declined(slots):
            return "declined"
        if self.skipped(slots):
            return "skipped"
        return None


@dataclass(frozen=True)
class ObjectiveChain:
    title: str
    objectives: tuple[Objective, ...]

    def __getitem__(self, name: str) -> Objective:
        return next(o for o in self.objectives if o.name == name)

    def current(self, slots: Slots) -> Objective | None:
        return next((o for o in self.objectives if o.behind(slots) is None), None)

    @cache  # noqa: B019 (chains are module constants)
    def playbook(self, medium: Medium) -> str:
        """Every objective in order, with how to go about it: the same text every turn."""
        steps = "\n\n".join(
            f"### {i}. {o.label}\n\n{guide(o.words, medium)}"
            for i, o in enumerate(self.objectives, 1)
        )
        return (
            f"# {self.title}: the objectives\n\n"
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
            how, got = o.behind(slots), o.got(slots)
            if how == "done":
                behind.append(f"{o.label} ({got})" if got else o.label)
            else:
                why = "they'd rather not" if how == "declined" else "skipped"
                behind.append(f"{o.label} ({why})")
        done = f" Done: {'; '.join(behind)}." if behind else ""
        where = f"you're on {now.label}." if now else "it's all behind them."
        return f"Where you are in {self.title.lower()}: {where}{done}"

    def move(self, before: Slots, after: Slots) -> ObjectiveMoved | None:
        """The move a change of facts makes, if it makes one. Only forward: a chain that goes
        back (a Google they'd said no to, then wanted) has nothing to announce."""
        was, now = self.current(before), self.current(after)
        if was is None or was is now:
            return None
        how = was.behind(after)
        if how is None:
            return None
        return ObjectiveMoved(
            left=was.name, how=how, got=was.got(after), now=now.name if now else None
        )

    def announcement(self, moved: ObjectiveMoved) -> str | None:
        """What a call hears when it moves: the objective's own words for how it went, if any."""
        words = self[moved.left].words.get(moved.how)
        return words.format(got=moved.got) if words else None


ONBOARDING = ObjectiveChain(
    "Onboarding",
    (
        Objective(
            "agent_name",
            done=lambda s: s.agent_name is not None,
            got=lambda s: s.agent_name,
            # Nice to have: someone who led with a real task and gave their name isn't held up.
            skipped=lambda s: s.help_need is not None and s.user_name is not None,
        ),
        Objective("user_name", done=lambda s: s.user_name is not None, got=lambda s: s.user_name),
        Objective(
            "google",
            done=lambda s: s.gmail in (GmailPhase.CONNECTED, GmailPhase.DISCONNECTED),
            got=lambda s: s.gmail_email if s.gmail is GmailPhase.CONNECTED else None,
        ),
        Objective("help_need", done=lambda s: s.help_need is not None, got=lambda s: s.help_need),
        Objective("wrap_up", done=lambda s: s.graduated),
    ),
)
