"""Onboarding as a list of objectives. Exactly one is open at a time, and only its guidance
is in front of the agent, so each step can carry explicit scripts without them leaking into
every other moment.

An objective is code (when it is done, which scenario applies, how many asks it gets) plus
words in prompts/objectives/<name>.md:

    The move, in words. Always shown.
    ## by text        shown only by text
    ## on a call      shown only on a call
    ## script         lines to say; code picks one per user
    ## script: <scenario>   lines for a named scenario, used instead of `script`

Scripts are for the clean case only: fixed moments that are the same for everyone (the
opener, asking for a name), shown the first time the step comes up and never right after a
call. Anywhere else a script gets forced into a moment it doesn't fit, so steps that depend
on what they said (their need, Gmail) have none. Scripts come in variants; the code picks one
per user (seeded by phone and objective), so three users still hear three different openers.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.agent.events import CallOptOut, Graduated, SlotChanged
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.events.event import Event
from app.gmail.events import GmailEvent
from app.text.events import AgentMessage, ReplyStarted
from app.users.user import Medium, User
from app.voice.call_state import CallEvent, CallTransition
from app.voice.events import Speaker, VoiceUtterance

PROGRESS = (SlotChanged, GmailEvent, CallOptOut, Graduated)  # a step moved; asks restart


@dataclass(frozen=True)
class Situation:
    """Everything an objective may look at."""

    slots: Slots
    medium: Medium
    first_reply: bool = False
    asks: int = 0  # agent turns since onboarding last moved forward
    said: tuple[str, ...] = ()  # the agent's recent lines, lowercased, so scripts don't repeat
    after_call: bool = False  # a call just ended and nothing has been texted since


@dataclass(frozen=True)
class Objective:
    name: str
    done: Callable[[Situation], bool]
    max_asks: int | None = None  # after this many turns without progress, park it for now
    scenarios: Sequence[tuple[str, Callable[[Situation], bool]]] = field(default=())

    def scenario(self, s: Situation) -> str | None:
        return next((name for name, applies in self.scenarios if applies(s)), None)


OBJECTIVES: tuple[Objective, ...] = (
    Objective("opener", done=lambda s: not s.first_reply),
    # Names are never parked: everything after needs them, and the turns before the name
    # (the opener, the call offer) would count against it.
    Objective(
        "agent_name",
        done=lambda s: s.slots.agent_name is not None,
        scenarios=(("no call", lambda s: s.slots.no_calls and s.medium is Medium.TEXT),),
    ),
    # Right after the name: point them at the contact card, once, so it's sorted before
    # moving on. One turn, then it parks whether or not they saved it.
    Objective(
        "contact",
        # The agent can't see whether they saved it (that's on their phone): one mention,
        # then it parks, and it's behind them once they've given their own name.
        done=lambda s: s.slots.user_name is not None,
        max_asks=1,
        scenarios=(("on a call", lambda s: s.medium is Medium.VOICE),),
    ),
    Objective("user_name", done=lambda s: s.slots.user_name is not None),
    # Asks count from the last saved step, so these count only their own turns.
    Objective("help_need", done=lambda s: s.slots.help_need is not None, max_asks=3),
    Objective("gmail", done=lambda s: s.slots.gmail is not None, max_asks=3),
    Objective("wrap_up", done=lambda s: s.slots.graduated),
)


def asks_since_progress(events: Sequence[Event]) -> int:
    """Text replies since the last event that moved a step. Spoken turns don't count: a call
    is a flowing conversation, and minutes of banter would park a step nobody asked about."""
    asks = 0
    for event in reversed(events):
        payload = event.payload
        if isinstance(payload, PROGRESS):
            break
        if isinstance(payload, ReplyStarted):
            asks += 1
    return asks


def current(s: Situation) -> tuple[Objective, bool] | None:
    """The open objective, and whether earlier ones were parked on the way to it. A parked
    objective used up its asks; the next one counts only the turns after that."""
    parked, asks = False, s.asks
    for objective in OBJECTIVES:
        if objective.done(s):
            continue
        if objective.max_asks is not None and asks >= objective.max_asks:
            parked = parked or objective.name != "contact"  # the card needs no "move on"
            asks -= objective.max_asks
            continue
        return objective, parked
    return None


def render(s: Situation, phone: str, texts: dict[str, dict[str, str]]) -> str:
    """The guidance for the open objective, for this channel and scenario. On a call it also
    carries the step after it: when a step finishes mid-turn, the voice can go straight on
    instead of improvising until the next picture of where things stand arrives."""
    found = current(s)
    if found is None:
        return ""
    objective, parked = found
    parts: list[str] = []
    if s.after_call:
        parts.append(
            "A call just ended. Before anything else, pick up from where the call left off, "
            "the way a person would after hanging up; the step below comes after that."
        )
    if parked:
        parts.append(
            "You've asked about the earlier step enough for now; leave it and move on. "
            "Pick it up only if they bring it up."
        )
    parts += _block(objective, s, phone, texts)
    following = _after(objective, s) if s.medium is Medium.VOICE else None
    if following is not None:
        parts.append(
            "Once that's actually settled (they've agreed or answered, not just heard a "
            "suggestion), go straight on to this without waiting for another turn:"
        )
        parts += _block(following, s, phone, texts)
    return "\n\n".join(parts)


def _block(
    objective: Objective, s: Situation, phone: str, texts: dict[str, dict[str, str]]
) -> list[str]:
    sections = texts[objective.name]
    parts = [sections[""]]
    channel = "on a call" if s.medium is Medium.VOICE else "by text"
    if channel in sections:
        parts.append(sections[channel])
    scenario = objective.scenario(s)
    script = sections.get(f"script: {scenario}") if scenario else None
    script = script or sections.get("script")
    if script and s.asks == 0 and not s.after_call:  # the clean case only
        fresh = [v for v in variants(script) if _norm(v) not in s.said]
        if not fresh:
            parts.append("You've already asked this in those words; ask differently this time.")
            return parts
        line = pick_from(fresh, f"{phone}:{objective.name}:{scenario or ''}")
        line = line.replace("[name]", s.slots.user_name or "(their name)")
        parts.append(
            "When you ask this, use this line, fitted naturally to the moment and said in the "
            "language you're speaking with them. If something else needs handling first (they "
            "went off topic, asked you something, a call just ended), handle that first and "
            "bring this in after:\n" + line
        )
    return parts


def _norm(line: str) -> str:
    return " ".join(line.lower().replace("[name]", "").split())[:60]


def _after(objective: Objective, s: Situation) -> Objective | None:
    later = OBJECTIVES[OBJECTIVES.index(objective) + 1 :]
    return next((o for o in later if not o.done(s)), None)


def guidance(
    user: User, events: Sequence[Event], medium: Medium, *, first_reply: bool = False
) -> str:
    """The open objective's guidance for this user, from their state and log."""
    s = Situation(
        slots=user.slots,
        medium=medium,
        first_reply=first_reply,
        asks=asks_since_progress(events),
        said=tuple(_norm(t) for t in _agent_lines(events)),
        after_call=medium is Medium.TEXT and _call_just_ended(events),
    )
    return render(s, user.phone, OBJECTIVE_TEXTS)


def _agent_lines(events: Sequence[Event]) -> list[str]:
    out: list[str] = []
    for event in events:
        p = event.payload
        if isinstance(p, AgentMessage) or (
            isinstance(p, VoiceUtterance) and p.speaker is Speaker.AGENT and p.text
        ):
            out.append(p.text or "")
    return out


def _call_just_ended(events: Sequence[Event]) -> bool:
    """The latest call event is its end, and nothing was texted to them after it."""
    for event in reversed(events):
        p = event.payload
        if isinstance(p, AgentMessage):
            return False
        if isinstance(p, CallEvent):
            return p.transition is CallTransition.ENDED
    return False


def variants(script: str) -> list[str]:
    """A script's variants: one per `- ` bullet."""
    return [ln[2:].strip() for ln in script.splitlines() if ln.startswith("- ")] or [script]


def pick_from(options: Sequence[str], seed: str) -> str:
    return options[zlib.crc32(seed.encode()) % len(options)]
