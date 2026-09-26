"""The voice responder for GPT-Live. There is no cancel: interrupt = say it now,
absorb = silent note, defer = wait for the inferred turn boundary, then say it.
The run is inferred and fed by the voice handler. Note wording is injected
(see app/agent/notes.py) so this module knows nothing about the domain."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.events.event import Event
from app.routing.responder import Responder
from app.routing.router import Clock
from app.routing.types import Medium, Run, Verb


@dataclass(frozen=True)
class Note:
    text: str
    speak: bool


NoteRenderer = Callable[[Event], Note | None]


class VoiceSink(Protocol):
    async def send(self, text: str, *, speak: bool) -> None: ...


class VoiceResponder(Responder):
    medium: Medium = Medium.VOICE

    def __init__(self, *, sink: VoiceSink, notes: NoteRenderer, clock: Clock) -> None:
        self._sink = sink
        self._notes = notes
        self._clock = clock
        self._run: Run | None = None
        self._deferred: list[Event] = []
        self._asked_question = False

    @property
    def run(self) -> Run | None:
        return self._run

    # ---- fed by the voice handler -----------------------------------------

    def on_agent_speaking(self) -> None:
        if self._run is None:
            self._run = Run(
                medium=Medium.VOICE,
                started=self._clock(),
                inferred=True,
                last_agent_turn_was_question=self._asked_question,
            )

    def on_delegation(self, in_flight: bool) -> None:
        if self._run:
            self._run = self._run.model_copy(update={"side_effect_in_flight": in_flight})

    async def on_turn_complete(self, *, asked_question: bool = False) -> None:
        self._run = None
        self._asked_question = asked_question
        deferred, self._deferred = self._deferred, []
        for event in deferred:
            await self._inject(event, speak=True)

    # ---- responder protocol ---------------------------------------------------

    async def start(self, event: Event) -> None:
        await self._inject(event)

    async def apply(self, verb: Verb, event: Event) -> None:
        match verb:
            case Verb.INTERRUPT:
                await self._inject(event, speak=True)
            case Verb.ABSORB:
                await self._inject(event, speak=False)
            case Verb.DEFER:
                self._deferred.append(event)
            case Verb.START:
                raise ValueError("START is not a verb a responder applies")

    async def _inject(self, event: Event, *, speak: bool | None = None) -> None:
        note = self._notes(event)
        if note is None:
            return
        await self._sink.send(note.text, speak=note.speak if speak is None else speak)
