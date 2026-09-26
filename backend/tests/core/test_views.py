from __future__ import annotations

from datetime import UTC, datetime

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart

from app.core.store import Store
from app.core.types import (
    AgentMessage,
    CallEvent,
    CallState,
    Decision,
    GmailEvent,
    Slots,
    ToolCall,
    UserMessage,
    VoiceUtterance,
)
from app.core.views import merge_turns, state_block, to_model_messages, trim_history
from tests.core.conftest import FakeClock


async def seeded(clock: FakeClock) -> Store:
    s = Store("p", clock=clock)
    await s.append("user", "text", UserMessage(text="hey"))
    await s.append("user", "text", UserMessage(text="you there?"))
    await s.append(
        "text_agent", "text", AgentMessage(text="hey! what should I call you?", via="text")
    )
    await s.append("call", "system", CallEvent(phase="ringing", initiated_by="agent"))
    await s.append("call", "system", CallEvent(phase="connecting"))
    await s.append("call", "system", CallEvent(phase="connected", call_id="c1"))
    await s.append(
        "voice_agent",
        "voice",
        VoiceUtterance(speaker="agent", text="hi, it's Jarvis", turn_id="t1"),
    )
    await s.append(
        "voice_agent", "voice", VoiceUtterance(speaker="user", text="it's Siobhan", turn_id="t2")
    )
    await s.append(
        "voice_agent", "voice", VoiceUtterance(speaker="user", text="S-I-O-B-H-A-N", turn_id="t3")
    )
    await s.append(
        "voice_agent",
        "voice",
        ToolCall(name="set_user_name", args={"name": "Siobhan"}, result={"ok": True}),
    )
    await s.append(
        "system",
        "system",
        Decision(trigger_kind="typing", verb="absorb", by="default", confidence=0.5, ms=1),
    )
    await s.append("google", "system", GmailEvent(phase="connected", email="s@x.com"))
    await s.append("call", "system", CallEvent(phase="ended", reason="user_hangup"))
    return s


async def test_merge_rule_joins_adjacent_same_role(clock: FakeClock) -> None:
    s = await seeded(clock)
    turns = merge_turns(s.events)
    roles = [t.role for t in turns]
    assert roles == ["user", "assistant", "note"]
    assert turns[0].text == "hey\nyou there?"
    note = turns[2].text
    assert "you started calling the user" in note
    assert "connecting" not in note
    assert "on call, you said: hi, it's Jarvis" in note
    assert "on call, user said: it's Siobhan\non call, user said: S-I-O-B-H-A-N" in note
    assert "you called set_user_name(name='Siobhan') → {'ok': True}" in note
    assert "Gmail connected as s@x.com" in note
    assert "call ended 12:00, reason: user_hangup" in note
    assert "decision" not in note.lower()


async def test_model_messages_alternate_and_bracket_notes(clock: FakeClock) -> None:
    s = await seeded(clock)
    msgs = to_model_messages(s.events)
    assert [type(m) for m in msgs] == [ModelRequest, ModelResponse, ModelRequest]
    last = msgs[-1]
    assert isinstance(last, ModelRequest)
    content = last.parts[0].content  # type: ignore[union-attr]
    assert isinstance(content, str)
    assert content.startswith("[note: ") and "\n[note: " in content


async def test_voice_texting_appears_as_note(clock: FakeClock) -> None:
    s = Store("p", clock=clock)
    await s.append("voice_agent", "text", AgentMessage(text="here's the link", via="voice"))
    assert merge_turns(s.events)[0].role == "note"


def test_trim_history_keeps_recent_tail() -> None:
    msgs: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("x" * 40)]) for _ in range(10)]
    kept = trim_history(msgs, max_messages=4, max_tokens=10_000)
    assert len(kept) == 4
    kept = trim_history(msgs, max_messages=100, max_tokens=25)  # 10 tokens each
    assert len(kept) == 2


def test_state_block_lists_missing() -> None:
    block = state_block(Slots(agent_name="Jarvis"), CallState(phase="none"))
    assert "agent_name: Jarvis" in block
    assert block.endswith("still need: user_name, help_need, gmail")
    done = state_block(
        Slots(agent_name="J", user_name="S", help_need="inbox", gmail="skipped"),
        CallState(phase="ended", reason="user_hangup"),
    )
    assert done.endswith("still need: nothing")
    assert "call: ended, reason: user_hangup" in done
    assert datetime.now(UTC).tzinfo is UTC
