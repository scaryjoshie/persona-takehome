from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart

from app.agent.context import to_model_messages, trim_history, turns, what_you_know
from app.agent.events import ToolCall
from app.agent.slots import Slots
from app.events.decision import Decision
from app.events.event import Event
from app.events.payload import Channel, Origin, Role
from app.gmail.events import GmailEvent, GmailPhase
from app.text.events import AgentMessage, UserMessage
from app.voice.call_events import CallEvent, CallTransition
from app.voice.call_state import CallPhase, CallState
from app.voice.events import Speaker, VoiceUtterance
from tests.conftest import ev

C, S, V = Channel.SYSTEM, Origin.SYSTEM, Origin.VOICE_AGENT


def seeded() -> list[Event]:
    return [
        ev(UserMessage(text="hey")),
        ev(UserMessage(text="you there?")),
        ev(AgentMessage(text="hey! what should I call you?"), Origin.TEXT_AGENT),
        ev(CallEvent(transition=CallTransition.RINGING), Origin.CALL, C),
        ev(CallEvent(transition=CallTransition.CONNECTING), Origin.CALL, C),
        ev(CallEvent(transition=CallTransition.CONNECTED), Origin.CALL, C),
        ev(
            VoiceUtterance(speaker=Speaker.AGENT, text="hi, it's Jarvis", turn_id="1"),
            V,
            Channel.VOICE,
        ),
        ev(
            VoiceUtterance(speaker=Speaker.USER, text="it's Siobhan", turn_id="2"), V, Channel.VOICE
        ),
        ev(
            VoiceUtterance(speaker=Speaker.USER, text="S-I-O-B-H-A-N", turn_id="3"),
            V,
            Channel.VOICE,
        ),
        ev(
            ToolCall(name="set_user_name", args={"name": "Siobhan"}, result={"ok": True}),
            V,
            Channel.VOICE,
        ),
        ev(
            Decision(trigger_kind="typing", verb="absorb", by="fallback"),
            S,
            C,
        ),
        ev(GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com"), Origin.GOOGLE, C),
        ev(CallEvent(transition=CallTransition.ENDED, reason="user_hangup"), Origin.CALL, C),
    ]


def test_events_render_themselves_and_merge() -> None:
    t = turns(seeded())
    assert [x.role for x in t] == [Role.USER, Role.ASSISTANT, Role.NOTE]
    assert t[0].text == "hey\nyou there?"
    note = t[2].text
    for expected in (
        "you started calling the user 12:00",
        "on call, you said: hi, it's Jarvis",
        "on call, user said: it's Siobhan\non call, user said: S-I-O-B-H-A-N",
        "you called set_user_name(name='Siobhan') → {'ok': True}",
        "Gmail connected as s@x.com",
        "call ended 12:00, reason: user_hangup",
    ):
        assert expected in note
    assert "connecting" not in note and "decision" not in note


def test_model_messages_alternate_and_bracket_notes() -> None:
    msgs = to_model_messages(seeded())
    assert [type(m) for m in msgs] == [ModelRequest, ModelResponse, ModelRequest]
    last = msgs[-1]
    assert isinstance(last, ModelRequest)
    part = last.parts[0]
    assert isinstance(part, UserPromptPart) and isinstance(part.content, str)
    assert part.content.startswith("[note: ") and "\n[note: " in part.content


def test_trim_history_keeps_the_tail() -> None:
    msgs: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("x" * 40)]) for _ in range(10)]
    assert len(trim_history(msgs, max_messages=4, max_tokens=10_000)) == 4
    assert len(trim_history(msgs, max_messages=100, max_tokens=25)) == 2


def test_what_you_know_is_plain_sentences() -> None:
    text = what_you_know(Slots(agent_name="Jarvis"), CallState())
    assert "Your name is Jarvis." in text and "You don't know their name yet." in text
    assert text.endswith("Still missing: user name, help need, gmail.")
    done = what_you_know(
        Slots(agent_name="J", user_name="S", help_need="inbox", gmail=GmailPhase.SKIPPED),
        CallState(phase=CallPhase.CONNECTED),
    )
    assert "They said no to Gmail." in done and "on a call with them right now" in done
    assert done.endswith("You have everything onboarding needs.")
