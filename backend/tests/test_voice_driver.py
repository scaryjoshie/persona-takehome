from __future__ import annotations

from app.agent.notes import note_for
from app.events.base import Channel, Origin
from app.gmail.types import GmailEvent, GmailPhase
from app.routing.types import Verb
from app.voice.driver import VoiceDriver
from tests.conftest import FakeClock, ev, typing, user_text


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, text: str, *, speak: bool) -> None:
        self.sent.append((text, speak))


def make(clock: FakeClock) -> tuple[VoiceDriver, Sink]:
    sink = Sink()
    return VoiceDriver(sink=sink, notes=note_for, clock=clock), sink


async def test_text_during_call_is_a_silent_note(clock: FakeClock) -> None:
    driver, sink = make(clock)
    await driver.start(user_text("here's my email btw"))
    assert len(sink.sent) == 1 and "texted" in sink.sent[0][0] and sink.sent[0][1] is False


async def test_interrupt_speaks_absorb_is_silent(clock: FakeClock) -> None:
    driver, sink = make(clock)
    driver.on_agent_speaking()
    assert driver.run is not None and driver.run.inferred
    await driver.apply(Verb.INTERRUPT, typing(True, 2))
    await driver.apply(Verb.ABSORB, typing(True, 3))
    assert [speak for _, speak in sink.sent] == [True, False]


async def test_defer_waits_for_turn_boundary(clock: FakeClock) -> None:
    driver, sink = make(clock)
    driver.on_agent_speaking()
    await driver.apply(
        Verb.DEFER,
        ev(GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c"), Origin.GOOGLE, Channel.SYSTEM),
    )
    assert sink.sent == []
    await driver.on_turn_complete(asked_question=True)
    assert driver.run is None and sink.sent[0][1] is True
    driver.on_agent_speaking()
    assert driver.run is not None and driver.run.last_agent_turn_was_question


async def test_typing_stopped_injects_nothing(clock: FakeClock) -> None:
    driver, sink = make(clock)
    await driver.start(typing(False))
    assert sink.sent == []
