from __future__ import annotations

from datetime import datetime

from app.core.deciders import DefaultDecider
from app.core.router import Router
from app.core.store import Store
from app.core.types import (
    CallEvent,
    GmailEvent,
    Inbound,
    Medium,
    Run,
    Typing,
    UserMessage,
    Verb,
)
from tests.core.conftest import FakeClock


class FakeHead:
    def __init__(self, medium: Medium, clock: FakeClock) -> None:
        self.medium: Medium = medium
        self._run: Run | None = None
        self._clock = clock
        self.log: list[tuple[str, str]] = []

    @property
    def run(self) -> Run | None:
        return self._run

    def begin(self, *, side_effect: bool = False, question: bool = False) -> None:
        self._run = Run(
            medium=self.medium,
            started=self._clock(),
            side_effect_in_flight=side_effect,
            last_agent_turn_was_question=question,
        )

    async def start(self, inbound: Inbound) -> None:
        self.log.append(("start", inbound.delta.kind))
        self.begin()

    async def apply(self, verb: Verb, inbound: Inbound) -> None:
        self.log.append((verb, inbound.delta.kind))


def make(clock: FakeClock) -> tuple[Store, FakeHead, FakeHead, Router]:
    store = Store("p", clock=clock)
    text, voice = FakeHead("text", clock), FakeHead("voice", clock)
    router = Router(store, {"text": text, "voice": voice}, DefaultDecider())
    return store, text, voice, router


def user(text: str) -> Inbound:
    return Inbound(origin="user", channel="text", delta=UserMessage(text=text))


async def test_message_when_idle_starts_text_run(clock: FakeClock) -> None:
    store, text, voice, router = make(clock)
    d = await router.route(user("hey"))
    assert d is not None and d.verb == "start" and d.by == "fixed"
    assert text.log == [("start", "user_message")]
    assert voice.log == []
    assert store.events[-1].payload.kind == "decision"


async def test_message_during_text_run_interrupts(clock: FakeClock) -> None:
    _, text, _, router = make(clock)
    text.begin()
    d = await router.route(user("wait actually"))
    assert d is not None and d.verb == "interrupt" and d.by == "fixed"
    assert text.log == [("interrupt", "user_message")]


async def test_interrupt_degrades_to_defer_when_side_effect_in_flight(clock: FakeClock) -> None:
    _, text, _, router = make(clock)
    text.begin(side_effect=True)
    d = await router.route(user("nvm"))
    assert d is not None and d.verb == "defer"
    assert d.note is not None and "side effect" in d.note


async def test_floor_voice_routes_to_voice_head(clock: FakeClock) -> None:
    store, text, voice, router = make(clock)
    await store.set_floor("voice")
    await router.route(user("texting during the call"))
    assert voice.log == [("start", "user_message")]
    assert text.log == []


async def test_floor_holder_output_is_never_routed(clock: FakeClock) -> None:
    store, text, voice, router = make(clock)
    await store.set_floor("voice")
    d = await router.route(
        Inbound(origin="voice_agent", channel="voice", delta=UserMessage(text="echo"))
    )
    assert d is None
    assert voice.log == [] and text.log == []


async def test_typing_during_voice_run_defaults_to_absorb(clock: FakeClock) -> None:
    store, _, voice, router = make(clock)
    await store.set_floor("voice")
    voice.begin(question=True)
    d = await router.route(
        Inbound(origin="user", channel="text", delta=Typing(active=True, seconds=2.5))
    )
    assert d is not None and d.verb == "absorb" and d.by == "default"
    assert voice.log == [("absorb", "typing")]


async def test_gmail_connected_during_voice_run_defers_by_default(clock: FakeClock) -> None:
    store, _, voice, router = make(clock)
    await store.set_floor("voice")
    voice.begin()
    d = await router.route(
        Inbound(
            origin="google", channel="system", delta=GmailEvent(phase="connected", email="a@b.c")
        )
    )
    assert d is not None and d.verb == "defer" and d.by == "default"


async def test_gmail_connected_when_voice_idle_starts_a_run(clock: FakeClock) -> None:
    store, _, voice, router = make(clock)
    await store.set_floor("voice")
    d = await router.route(
        Inbound(
            origin="google", channel="system", delta=GmailEvent(phase="connected", email="a@b.c")
        )
    )
    assert d is not None and d.verb == "start"
    assert voice.log == [("start", "gmail")]


async def test_hangup_after_floor_flip_interrupts_text(clock: FakeClock) -> None:
    store, text, _, router = make(clock)
    # The call manager flips the floor in the same step it appends the ended event.
    await store.set_floor("text")
    text.begin()
    d = await router.route(
        Inbound(
            origin="call", channel="system", delta=CallEvent(phase="ended", reason="user_hangup")
        )
    )
    assert d is not None and d.verb == "interrupt"
    assert text.log == [("interrupt", "call")]


async def test_ringing_is_context_not_a_reply(clock: FakeClock) -> None:
    _, text, _, router = make(clock)
    text.begin()
    d = await router.route(
        Inbound(origin="call", channel="system", delta=CallEvent(phase="ringing"))
    )
    assert d is not None and d.verb == "absorb"


async def test_decision_latency_is_measured(clock: FakeClock) -> None:
    _, text, _, router = make(clock)

    class SlowHead(FakeHead):
        async def start(self, inbound: Inbound) -> None:
            clock.advance(0.25)
            await super().start(inbound)

    slow = SlowHead("text", clock)
    store = Store("p", clock=clock)
    router = Router(store, {"text": slow, "voice": text}, DefaultDecider())
    d = await router.route(user("x"))
    assert d is not None and d.ms == 250
    assert isinstance(d.ms, int) and isinstance(store.now(), datetime)
