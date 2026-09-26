from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart

from app.agent.types import Slots, ToolCall
from app.agent.views import state_block, to_model_messages, trim_history, turns
from app.calls.types import CallEvent, CallPhase, CallState, CallTransition
from app.events.base import Channel, Origin, Role
from app.events.store import Store
from app.gmail.types import GmailEvent, GmailPhase
from app.routing.types import DecidedBy, Decision, Verb
from app.text.types import AgentMessage, UserMessage
from app.voice.types import Speaker, VoiceUtterance


async def seeded(store: Store) -> Store:
    s = store
    await s.append(Origin.USER, Channel.TEXT, UserMessage(text="hey"))
    await s.append(Origin.USER, Channel.TEXT, UserMessage(text="you there?"))
    await s.append(
        Origin.TEXT_AGENT, Channel.TEXT, AgentMessage(text="hey! what should I call you?")
    )
    await s.append(Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.RINGING))
    await s.append(Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.CONNECTING))
    await s.append(Origin.CALL, Channel.SYSTEM, CallEvent(transition=CallTransition.CONNECTED))
    await s.append(
        Origin.VOICE_AGENT,
        Channel.VOICE,
        VoiceUtterance(speaker=Speaker.AGENT, text="hi, it's Jarvis", turn_id="1"),
    )
    await s.append(
        Origin.VOICE_AGENT,
        Channel.VOICE,
        VoiceUtterance(speaker=Speaker.USER, text="it's Siobhan", turn_id="2"),
    )
    await s.append(
        Origin.VOICE_AGENT,
        Channel.VOICE,
        VoiceUtterance(speaker=Speaker.USER, text="S-I-O-B-H-A-N", turn_id="3"),
    )
    await s.append(
        Origin.VOICE_AGENT,
        Channel.VOICE,
        ToolCall(name="set_user_name", args={"name": "Siobhan"}, result={"ok": True}),
    )
    await s.append(
        Origin.SYSTEM,
        Channel.SYSTEM,
        Decision(
            trigger_kind="typing", verb=Verb.ABSORB, by=DecidedBy.DEFAULT, confidence=0.5, ms=1
        ),
    )
    await s.append(
        Origin.GOOGLE, Channel.SYSTEM, GmailEvent(phase=GmailPhase.CONNECTED, email="s@x.com")
    )
    await s.append(
        Origin.CALL,
        Channel.SYSTEM,
        CallEvent(transition=CallTransition.ENDED, reason="user_hangup"),
    )
    return s


async def test_events_render_themselves_and_merge(store: Store) -> None:
    s = await seeded(store)
    t = turns(s.events)
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


async def test_model_messages_alternate_and_bracket_notes(store: Store) -> None:
    msgs = to_model_messages((await seeded(store)).events)
    assert [type(m) for m in msgs] == [ModelRequest, ModelResponse, ModelRequest]
    last = msgs[-1]
    assert isinstance(last, ModelRequest)
    part = last.parts[0]
    assert isinstance(part, UserPromptPart) and isinstance(part.content, str)
    assert part.content.startswith("[note: ") and "\n[note: " in part.content


def test_trim_history_keeps_the_tail() -> None:
    msgs: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("x" * 40)]) for _ in range(10)]
    assert len(trim_history(msgs, max_messages=4, max_tokens=10_000)) == 4
    assert len(trim_history(msgs, max_messages=100, max_tokens=25)) == 2  # 10 tokens each


def test_state_block() -> None:
    block = state_block(Slots(agent_name="Jarvis"), CallState())
    assert "agent_name: Jarvis" in block and block.endswith(
        "still need: user_name, help_need, gmail"
    )
    done = state_block(
        Slots(agent_name="J", user_name="S", help_need="inbox", gmail=GmailPhase.SKIPPED),
        CallState(phase=CallPhase.ENDED, reason="user_hangup"),
    )
    assert done.endswith("still need: nothing") and "call: ended, reason: user_hangup" in done
