from __future__ import annotations

from app.agent.objectives import ONBOARDING
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.google.events import GmailPhase
from app.pipeline import Pipeline
from app.users.user import Medium


def now_on(slots: Slots, *, first_reply: bool = False) -> str | None:
    found = ONBOARDING.current(slots, first_reply=first_reply)
    return found.name if found else None


def test_every_objective_has_a_label_and_words_and_no_file_is_orphaned() -> None:
    assert {o.name for o in ONBOARDING.objectives} == set(OBJECTIVE_TEXTS)
    assert all(o.label and OBJECTIVE_TEXTS[o.name][""] for o in ONBOARDING.objectives)


def test_the_chain_goes_in_order() -> None:
    assert now_on(Slots(), first_reply=True) == "intro"
    assert now_on(Slots()) == "agent_name"
    assert now_on(Slots(agent_name="Mila")) == "user_name"
    assert now_on(Slots(agent_name="Mila", user_name="Jo")) == "google"
    linked = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.LINK_SENT)
    assert now_on(linked) == "google"  # the link is seen through before what they need
    connected = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.CONNECTED)
    assert now_on(connected) == "help_need"
    done = Slots(agent_name="M", user_name="J", gmail=GmailPhase.SKIPPED, help_need="rent")
    assert now_on(done) == "wrap_up"
    assert ONBOARDING.render(done.model_copy(update={"graduated": True}), Medium.TEXT) == ""


def test_the_log_shows_where_you_are_and_only_the_current_one_in_full() -> None:
    slots = Slots(agent_name="Meeno", user_name="Joseph", set_aside=("help_need",))
    log = ONBOARDING.render(slots, Medium.VOICE)
    assert "✓ a name for you: Meeno" in log and "✓ their name: Joseph" in log
    assert "▶ their Google (you're here)" in log and "Right now: their Google." in log
    assert "– something they'd like help with: they'd rather not" in log
    assert "· wrap up" in log
    assert "i'll text you a link, cool?" in log  # its example, on a call
    assert "open invitation" not in log  # what's ahead is a label, not its words


def test_each_channel_gets_its_own_part_and_example() -> None:
    assert "Spell it back" in ONBOARDING.render(Slots(), Medium.VOICE)
    assert "Spell it back" not in ONBOARDING.render(Slots(), Medium.TEXT)
    assert "what do you wanna call me?" in ONBOARDING.render(Slots(), Medium.VOICE)
    assert "what do you want to name me?" in ONBOARDING.render(Slots(), Medium.TEXT)


def test_their_name_goes_into_the_example() -> None:
    slots = Slots(agent_name="Mila", user_name="Jo", gmail=GmailPhase.CONNECTED)
    assert "alright, Jo, now that setup's done" in ONBOARDING.render(slots, Medium.TEXT)


def test_a_task_first_user_is_not_held_up_for_the_agent_name() -> None:
    assert now_on(Slots(user_name="Sam", help_need="book a dentist")) == "google"


def test_an_objective_they_set_aside_is_passed_over() -> None:
    assert now_on(Slots(set_aside=("agent_name",))) == "user_name"
    declined = Slots(agent_name="M", user_name="J", gmail=GmailPhase.SKIPPED)
    assert now_on(declined) == "help_need"


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
