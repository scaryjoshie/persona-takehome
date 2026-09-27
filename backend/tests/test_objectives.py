from __future__ import annotations

from app.agent.objectives import OBJECTIVES, active, brief
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.pipeline import Pipeline
from app.users.user import Medium


def name_of(slots: Slots, *, first_reply: bool = False) -> str | None:
    found = active(slots, first_reply=first_reply)
    return found.name if found else None


def test_every_objective_has_words_and_no_file_is_orphaned() -> None:
    assert {o.name for o in OBJECTIVES} == set(OBJECTIVE_TEXTS)
    assert all(OBJECTIVE_TEXTS[o.name][""].startswith("Goal:") for o in OBJECTIVES)


def test_objectives_open_in_order() -> None:
    assert name_of(Slots(), first_reply=True) == "intro"
    assert name_of(Slots()) == "agent_name"
    assert name_of(Slots(agent_name="Mila")) == "user_name"
    assert name_of(Slots(agent_name="Mila", user_name="Jo")) == "google"
    linked = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.LINK_SENT)
    assert name_of(linked) == "google"  # the link is seen through before what they need
    connected = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.CONNECTED)
    assert name_of(connected) == "help_need"
    done = Slots(agent_name="M", user_name="J", gmail=GmailPhase.SKIPPED, help_need="rent")
    assert name_of(done) == "wrap_up"
    assert name_of(done.model_copy(update={"graduated": True})) is None
    assert brief(done.model_copy(update={"graduated": True}), Medium.TEXT) == ""


def test_only_the_active_objective_shows_with_its_example() -> None:
    text = brief(Slots(), Medium.TEXT)
    assert text.startswith("## Your objective") and "a name for you" in text
    assert "For example: you should give me a name" in text
    assert "Gmail" not in text and "what they'd like to be called" not in text  # nothing ahead


def test_each_channel_sees_its_own_part_and_example() -> None:
    by_text = brief(Slots(agent_name="Mila"), Medium.TEXT)
    on_call = brief(Slots(agent_name="Mila"), Medium.VOICE)
    assert "They typed it" in by_text and "They typed it" not in on_call
    assert "spell it back" in on_call and "spell it back" not in by_text
    assert "what do you wanna call me?" in brief(Slots(), Medium.VOICE)
    assert "what do you wanna call me?" not in brief(Slots(), Medium.TEXT)


def test_their_name_goes_into_the_example() -> None:
    slots = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.CONNECTED)
    assert "alright, Jo, now that setup's done" in brief(slots, Medium.TEXT)


def test_a_task_first_user_is_not_held_up_for_the_agent_name() -> None:
    assert name_of(Slots(user_name="Sam", help_need="book a dentist")) == "google"


def test_an_objective_they_set_aside_is_not_active() -> None:
    assert name_of(Slots(set_aside=("agent_name",))) == "user_name"
    declined = Slots(agent_name="M", user_name="J", gmail=GmailPhase.SKIPPED)
    assert name_of(declined) == "help_need"


async def test_set_aside_is_kept_and_shown_to_the_agent(pipeline: Pipeline) -> None:
    from app.agent.context import what_you_know
    from app.agent.events import StepSetAside
    from app.events.payload import Channel, Origin
    from tests.conftest import PHONE

    step = StepSetAside(step="user_name")
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, step)
    assert await pipeline.submit(PHONE, Origin.TEXT_AGENT, Channel.TEXT, step) is None  # once
    user = await pipeline.user(PHONE)
    assert user.slots.set_aside == ("user_name",)
    assert "rather not do this for now: giving their name" in what_you_know(user.slots, user.call)
