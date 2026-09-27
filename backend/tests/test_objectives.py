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
    assert "and what's your name?" in voice and "by text after the call" in voice
    text = ONBOARDING.playbook(Medium.TEXT)
    assert "by text after the call" not in text  # a call's part stays on calls
    assert "wanna call me" not in text  # a line wanted only on a call
    assert "{got}" not in voice and "Done" not in voice  # what's said on a move stays off the page


def test_the_pointer_says_where_you_are_and_what_is_behind() -> None:
    slots = Slots(agent_name="Meeno", user_name="Joseph", gmail=GmailPhase.SKIPPED)
    pointer = ONBOARDING.pointer(slots)
    assert pointer.startswith("Where you are in onboarding: you're on something")
    assert "a name for you (Meeno)" in pointer and "their name (Joseph)" in pointer
    assert "their Google (they'd rather not)" in pointer
    task_first = ONBOARDING.pointer(Slots(user_name="Sam", help_need="a dentist"))
    assert "a name for you (skipped)" in task_first  # never "done" without a name
    gone = Slots(agent_name="M", user_name="J", gmail=GmailPhase.DISCONNECTED, gmail_email="k@g")
    assert "k@g" not in ONBOARDING.pointer(gone)


def test_a_move_is_said_in_the_objectives_own_words() -> None:
    moved = ONBOARDING.move(Slots(), Slots(agent_name="Mino"))
    assert moved == ObjectiveMoved(left="agent_name", how="done", got="Mino", now="user_name")
    assert moved is not None
    said = ONBOARDING.announcement(moved)
    assert said is not None and said.startswith("You've got a name: Mino!")
    assert ONBOARDING.move(Slots(agent_name="Mino"), Slots(agent_name="Milo")) is None  # renamed
    aside = ONBOARDING.move(Slots(agent_name="M"), Slots(agent_name="M", set_aside=("user_name",)))
    assert aside is not None and aside.how == "declined"
    assert ONBOARDING.announcement(aside) is None  # nothing to say: they just said it
    skipped = ONBOARDING.move(Slots(user_name="Sam"), Slots(user_name="Sam", help_need="rent"))
    assert skipped is not None and skipped.how == "skipped" and skipped.got is None
    assert ONBOARDING.announcement(skipped) is None  # a name they didn't need isn't "done"


def test_a_move_only_ever_goes_forward() -> None:
    said_no = Slots(agent_name="M", user_name="J", gmail=GmailPhase.SKIPPED)
    wanted_after_all = said_no.model_copy(update={"gmail": GmailPhase.LINK_SENT})
    assert on(said_no) == "help_need" and on(wanted_after_all) == "google"
    assert ONBOARDING.move(said_no, wanted_after_all) is None  # nothing got done


async def test_the_pipeline_records_the_move_after_the_fact(pipeline: Pipeline) -> None:
    named = SlotChanged(slot="agent_name", new="Mino")
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, named)
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, StepSetAside(step="user_name"))
    history = await pipeline.history(PHONE)
    moves = [e.payload for e in history if isinstance(e.payload, ObjectiveMoved)]
    assert moves == [
        ObjectiveMoved(left="agent_name", how="done", got="Mino", now="user_name"),
        ObjectiveMoved(left="user_name", how="declined", now="google"),
    ]
    kinds = [e.kind for e in history]
    assert kinds == ["slot_changed", "objective_moved", "step_set_aside", "objective_moved"]


async def test_set_aside_is_kept_and_shown_to_the_agent(pipeline: Pipeline) -> None:
    step = StepSetAside(step="user_name")
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, step)
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, step) is None  # once
    named = SlotChanged(slot="agent_name", new="Mino")
    await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, named)
    user = await pipeline.user(PHONE)
    assert user.slots.set_aside == ("user_name",)
    assert "their name (they'd rather not)" in ONBOARDING.pointer(user.slots)
