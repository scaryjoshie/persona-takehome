from __future__ import annotations

from app.agent.call_notes import call_note
from app.events.payload import Channel, Origin
from app.gmail.events import GmailEvent, GmailPhase
from app.routing.types import Verb
from app.voice.responder import VoiceResponder
from tests.conftest import FakeClock, ev, typing, user_text


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, text: str, *, speak: bool) -> None:
        self.sent.append((text, speak))


def make(clock: FakeClock) -> tuple[VoiceResponder, Sink]:
    sink = Sink()
    return VoiceResponder(sink=sink, notes=call_note, clock=clock), sink


async def test_text_during_call_is_a_silent_note(clock: FakeClock) -> None:
    responder, sink = make(clock)
    await responder.start(user_text("here's my email btw"))
    assert len(sink.sent) == 1 and "texted" in sink.sent[0][0] and sink.sent[0][1] is False


async def test_interrupt_speaks_absorb_is_silent(clock: FakeClock) -> None:
    responder, sink = make(clock)
    responder.on_agent_speaking()
    assert responder.run is not None and responder.run.inferred
    await responder.apply(Verb.INTERRUPT, typing(True, 2))
    await responder.apply(Verb.ABSORB, typing(True, 3))
    assert [speak for _, speak in sink.sent] == [True, False]


async def test_defer_waits_for_turn_boundary(clock: FakeClock) -> None:
    responder, sink = make(clock)
    responder.on_agent_speaking()
    await responder.apply(
        Verb.DEFER,
        ev(GmailEvent(phase=GmailPhase.CONNECTED, email="a@b.c"), Origin.GOOGLE, Channel.SYSTEM),
    )
    assert sink.sent == []
    await responder.on_turn_complete(asked_question=True)
    assert responder.run is None and sink.sent[0][1] is True
    responder.on_agent_speaking()
    assert responder.run is not None and responder.run.last_agent_turn_was_question


async def test_typing_stopped_injects_nothing(clock: FakeClock) -> None:
    responder, sink = make(clock)
    await responder.start(typing(False))
    assert sink.sent == []
