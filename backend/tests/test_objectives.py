from __future__ import annotations

from app.agent.events import ObjectiveMoved, SlotChanged, StepSetAside
from app.agent.objectives import ONBOARDING
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.events.payload import Channel, Origin
from app.google.events import GmailPhase
from app.pipeline import Pipeline
from app.users.user import Medium
from tests.conftest import PHONE


def on(slots: Slots) -> str | None:
    found = ONBOARDING.current(slots)
    return found.name if found else None


def test_every_objective_has_a_label_and_words_and_no_file_is_orphaned() -> None:
    assert {o.name for o in ONBOARDING.objectives} == set(OBJECTIVE_TEXTS)
    assert all(o.label and OBJECTIVE_TEXTS[o.name][""] for o in ONBOARDING.objectives)


def test_the_pointer_moves_in_order() -> None:
    assert on(Slots()) == "agent_name"
    assert on(Slots(agent_name="Mila")) == "user_name"
    assert on(Slots(agent_name="Mila", user_name="Jo")) == "google"
    linked = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.LINK_SENT)
    assert on(linked) == "google"  # the link is seen through before what they need
    connected = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.CONNECTED)
    assert on(connected) == "help_need"
    done = Slots(agent_name="M", user_name="J", gmail=GmailPhase.SKIPPED, help_need="rent")
    assert on(done) == "wrap_up"
    assert on(done.model_copy(update={"graduated": True})) is None
    assert on(Slots(user_name="Sam", help_need="book a dentist")) == "google"  # task first
    assert on(Slots(set_aside=("agent_name",))) == "user_name"


def test_the_playbook_is_the_same_every_turn_and_has_every_objective() -> None:
    voice = ONBOARDING.playbook(Medium.VOICE)
    assert voice.index("a name for you") < voice.index("their name") < voice.index("their Google")
    assert "i need a name, so... what do you wanna call me?" in voice
    assert "and what's your name?" in voice and "J-O-N, right?" in voice
    assert "J-O-N, right?" not in ONBOARDING.playbook(Medium.TEXT)  # a call's part stays on calls


def test_the_pointer_says_where_you_are_and_what_is_behind() -> None:
    slots = Slots(agent_name="Meeno", user_name="Joseph", gmail=GmailPhase.SKIPPED)
    pointer = ONBOARDING.pointer(slots)
    assert pointer.startswith("Where you are in onboarding: you're on something")
    assert "a name for you (Meeno)" in pointer and "their name (Joseph)" in pointer
    assert "their Google (they'd rather not)" in pointer


def test_a_move_is_named_and_safe_to_hear_twice() -> None:
    moved = ONBOARDING.move(Slots(), Slots(agent_name="Mino"))
    assert moved == ObjectiveMoved(left="agent_name", now="user_name")
    assert moved is not None
    assert ONBOARDING.announcement(moved) == (
        "Done: a name for you. If you haven't already, now's the time to move on to their name."
    )
    assert ONBOARDING.move(Slots(agent_name="Mino"), Slots(agent_name="Milo")) is None  # renamed
    aside = ONBOARDING.move(Slots(agent_name="M"), Slots(agent_name="M", set_aside=("user_name",)))
    assert aside is not None and aside.set_aside


async def test_the_pipeline_records_the_move_after_the_fact(pipeline: Pipeline) -> None:
    named = SlotChanged(slot="agent_name", new="Mino")
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, named)
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, StepSetAside(step="user_name"))
    history = await pipeline.history(PHONE)
    moves = [e.payload for e in history if isinstance(e.payload, ObjectiveMoved)]
    assert moves == [
        ObjectiveMoved(left="agent_name", now="user_name"),
        ObjectiveMoved(left="user_name", now="google", set_aside=True),
    ]


async def test_set_aside_is_kept_and_shown_to_the_agent(pipeline: Pipeline) -> None:
    from app.agent.context import what_you_know

    step = StepSetAside(step="user_name")
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, step)
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, step) is None  # once
    user = await pipeline.user(PHONE)
    assert user.slots.set_aside == ("user_name",)
    assert "rather not do this for now: giving their name" in what_you_know(user.slots, user.call)
