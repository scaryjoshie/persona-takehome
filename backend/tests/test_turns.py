"""Stitching GPT-Live's turns back together."""

from __future__ import annotations

from app.voice.events import Speaker
from app.voice.turns import Line, TurnJoiner, join

U, A = Speaker.USER, Speaker.AGENT


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_join_spaces_words_but_not_punctuation() -> None:
    assert join("Hi", ". My name is Sam") == "Hi. My name is Sam"
    assert join("to call", "it") == "to call it"
    assert join("", "hey") == "hey"


def test_fragments_from_one_speaker_join_under_the_first_id() -> None:
    clock = Clock()
    j = TurnJoiner(quiet=1.2, clock=clock)
    assert j.final(U, "user-1", "Hi") == (Line(U, "user-1", "Hi"), None)
    clock.t = 0.5
    shown, closed = j.caption(U, "user-2", ". My name is")
    assert shown == Line(U, "user-1", "Hi. My name is") and closed is None
    clock.t = 3.0  # a long fragment still joins: it started inside the window
    line, closed = j.final(U, "user-2", ". My name is Siobhan")
    assert line == Line(U, "user-1", "Hi. My name is Siobhan") and closed is None
    assert j.due() is None  # quiet counts from the last finished fragment
    clock.t = 4.5
    assert j.due() == line and j.pending is None


def test_the_other_speaker_closes_the_turn() -> None:
    clock = Clock()
    j = TurnJoiner(quiet=1.2, clock=clock)
    j.final(U, "user-1", "I'm Robert")
    shown, closed = j.caption(A, "agent-2", "nice to")
    assert closed == Line(U, "user-1", "I'm Robert") and shown == Line(A, "agent-2", "nice to")


def test_a_quiet_gap_starts_a_new_turn() -> None:
    clock = Clock()
    j = TurnJoiner(quiet=1.2, clock=clock)
    j.final(A, "agent-1", "what's")
    clock.t = 0.2
    j.final(A, "agent-3", "one kind")
    assert j.due() is None  # not quiet long enough yet
    clock.t = 2.0
    shown, closed = j.caption(A, "agent-5", "hello?")
    assert closed == Line(A, "agent-1", "what's one kind")
    assert shown == Line(A, "agent-5", "hello?")


def test_nothing_is_due_while_a_joined_turn_is_still_being_spoken() -> None:
    clock = Clock()
    j = TurnJoiner(quiet=1.2, clock=clock)
    j.final(U, "user-1", "so")
    clock.t = 0.5
    j.caption(U, "user-2", "basically")
    clock.t = 5.0
    assert j.due() is None
    assert j.close() == Line(U, "user-1", "so basically")
