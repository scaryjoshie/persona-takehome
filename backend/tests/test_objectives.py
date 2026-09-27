"""Onboarding objectives: which one is open, and what the agent sees for it."""

from __future__ import annotations

from app.agent.events import SlotChanged
from app.agent.objectives import OBJECTIVES, Situation, asks_since_progress, current, render
from app.agent.prompts import OBJECTIVE_TEXTS
from app.agent.slots import Slots
from app.pipeline import Pipeline
from app.text.events import AgentMessage, ReplyStarted, UserMessage
from app.users.user import Medium
from app.voice.events import Speaker, VoiceUtterance
from tests.conftest import ev


def situation(medium: Medium = Medium.TEXT, asks: int = 0, **slots: object) -> Situation:
    return Situation(
        slots=Slots(**slots),  # pyright: ignore[reportArgumentType]
        medium=medium,
        asks=asks,
    )


def open_name(s: Situation) -> str | None:
    found = current(s)
    return found[0].name if found else None


def test_every_objective_has_words_and_no_file_is_orphaned() -> None:
    assert {o.name for o in OBJECTIVES} == set(OBJECTIVE_TEXTS)
    assert all(OBJECTIVE_TEXTS[o.name][""] for o in OBJECTIVES)


def test_objectives_open_in_order() -> None:
    assert open_name(Situation(Slots(), Medium.TEXT, first_reply=True)) == "opener"
    assert open_name(situation()) == "agent_name"
    assert open_name(situation(agent_name="Mila")) == "contact"
    assert open_name(situation(agent_name="Mila", asks=1)) == "user_name"
    assert open_name(situation(agent_name="Mila", user_name="Sam")) == "gmail"
    sent = situation(agent_name="M", user_name="S", gmail="link_sent")
    assert open_name(sent) == "gmail_check"  # the link is seen through before the next step
    never_came = situation(agent_name="M", user_name="S", gmail="link_sent", asks=1)
    assert open_name(never_came) == "help_need"
    assert open_name(situation(agent_name="M", user_name="S", gmail="connected")) == "help_need"
    done = situation(agent_name="M", user_name="S", help_need="x", gmail="skipped")
    assert open_name(done) == "wrap_up"
    assert (
        open_name(
            situation(agent_name="M", user_name="S", help_need="x", gmail="skipped", graduated=True)
        )
        is None
    )  # noqa: E501


def test_each_channel_sees_its_own_part() -> None:
    by_text = render(situation(agent_name="Mila", asks=1), "1", OBJECTIVE_TEXTS)
    on_call = render(situation(Medium.VOICE, agent_name="Mila", asks=1), "1", OBJECTIVE_TEXTS)
    assert "They typed it" in by_text and "texting how you spelled" not in by_text
    assert "texting how you spelled" in on_call and "They typed it" not in on_call


def test_scripts_are_picked_per_user_and_stable() -> None:
    lines = {render(situation(), phone, OBJECTIVE_TEXTS) for phone in ("1", "2", "3", "4", "5")}
    assert len(lines) > 1  # different users, different variants
    assert render(situation(), "1", OBJECTIVE_TEXTS) == render(situation(), "1", OBJECTIVE_TEXTS)


def test_a_scenario_swaps_the_script() -> None:
    text = render(situation(no_calls=True), "1", OBJECTIVE_TEXTS)
    assert "texting works" in text


def test_the_name_goes_into_the_script() -> None:
    texts = {name: dict(sections) for name, sections in OBJECTIVE_TEXTS.items()}
    texts["help_need"]["script"] = "- okay [name], what've you got?"
    text = render(situation(agent_name="Mila", user_name="Sam", gmail="skipped"), "1", texts)
    assert "okay Sam, what've you got?" in text


def test_a_step_that_keeps_stalling_is_parked() -> None:
    stuck = situation(agent_name="M", user_name="S", asks=2)
    assert open_name(stuck) == "gmail"
    assert "move on" in render(stuck, "1", OBJECTIVE_TEXTS)
    assert open_name(situation(agent_name="M", user_name="S", asks=6)) == "wrap_up"
    assert open_name(situation(asks=9)) == "agent_name"  # names are never parked


def test_asks_count_text_replies_sent_since_the_last_saved_step() -> None:
    events = [
        ev(ReplyStarted(through_seq=1)),
        ev(AgentMessage(text="what should i call you?")),
        ev(SlotChanged(slot="user_name", new="Sam")),
        ev(ReplyStarted(through_seq=3)),
        ev(AgentMessage(text="what's up this week?")),
        ev(VoiceUtterance(speaker=Speaker.AGENT, text="so?", turn_id="a")),
        ev(UserMessage(text="hmm")),
        ev(ReplyStarted(through_seq=7)),  # being written now: not an ask yet
    ]
    assert asks_since_progress(events) == 1  # spoken turns don't count


def test_on_a_call_the_next_step_comes_along() -> None:
    on_call = render(situation(Medium.VOICE, agent_name="Mila", asks=1), "1", OBJECTIVE_TEXTS)
    assert "Right now: their name" in on_call and "Right now: connecting their Google" in on_call
    by_text = render(situation(agent_name="Mila", asks=1), "1", OBJECTIVE_TEXTS)
    assert "Right now: connecting their Google" not in by_text


def test_a_line_already_said_is_not_scripted_again() -> None:
    lines = [v for v in OBJECTIVE_TEXTS["agent_name"]["script"].splitlines() if v.startswith("- ")]
    said = tuple(" ".join(v[2:].lower().split())[:60] for v in lines)
    text = render(Situation(Slots(), Medium.TEXT, said=said), "1", OBJECTIVE_TEXTS)
    assert "ask differently" in text


def test_after_a_call_the_text_picks_up_from_it() -> None:
    after = Situation(Slots(), Medium.TEXT, after_call=True)
    assert "A call just ended" in render(after, "1", OBJECTIVE_TEXTS)


def test_scripts_show_only_in_the_clean_case() -> None:
    first = render(situation(), "1", OBJECTIVE_TEXTS)
    again = render(situation(asks=1), "1", OBJECTIVE_TEXTS)
    after_call = render(Situation(Slots(), Medium.TEXT, after_call=True), "1", OBJECTIVE_TEXTS)
    assert "use this line" in first
    assert "use this line" not in again and "use this line" not in after_call


def test_the_ask_gets_an_angle_that_varies_by_user() -> None:
    angles = {
        render(situation(agent_name="M", user_name="S", gmail="skipped"), phone, OBJECTIVE_TEXTS)
        for phone in ("1", "2", "3", "4", "5", "6")
    }
    assert len(angles) > 1 and all("Your angle for this" in a for a in angles)


def test_after_a_question_nothing_gets_pushed() -> None:
    asked = Situation(Slots(), Medium.TEXT, they_asked=True)
    text = render(asked, "1", OBJECTIVE_TEXTS)
    assert "answer that" in text and "use this line" not in text


def test_a_task_first_user_is_not_held_up_for_the_agent_name() -> None:
    task_first = situation(user_name="Sam", help_need="landlord won't fix the heater")
    assert open_name(task_first) == "gmail"


def test_later_call_notes_carry_no_lines_to_repeat() -> None:
    first = render(situation(Medium.VOICE), "1", OBJECTIVE_TEXTS)
    later = render(Situation(Slots(), Medium.VOICE, scripts=False), "1", OBJECTIVE_TEXTS)
    assert "use this line" in first and "use this line" not in later


def test_a_link_that_never_connected_gets_a_check_before_wrapping_up() -> None:
    sent = situation(agent_name="M", user_name="S", help_need="bills", gmail="link_sent")
    assert open_name(sent) == "gmail_check"
    assert "doesn't come through" in render(sent, "1", OBJECTIVE_TEXTS)
    no = situation(agent_name="M", user_name="S", help_need="bills", gmail="skipped")
    assert open_name(no) == "wrap_up"


def test_once_gmail_connects_the_ask_is_open_and_nothing_reads_the_inbox() -> None:
    for medium in Medium:
        connected = situation(medium, agent_name="M", user_name="S", gmail="connected")
        assert open_name(connected) == "help_need"
        assert "anywhere in their life" in render(connected, "1", OBJECTIVE_TEXTS)
    wrapping = situation(agent_name="M", user_name="S", gmail="connected", help_need="bills")
    assert open_name(wrapping) == "wrap_up"
    assert "inbox" not in render(wrapping, "1", OBJECTIVE_TEXTS)


def test_a_step_they_set_aside_is_not_asked_for_again() -> None:
    assert open_name(situation(agent_name="Mila", asks=1)) == "user_name"
    declined = situation(agent_name="Mila", asks=1, set_aside=("user_name",))
    assert open_name(declined) == "gmail"
    assert "user_name" not in declined.slots.missing()


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
