from __future__ import annotations

from app.text.debounce import Debounce
from tests.conftest import FakeClock, user_text


def test_quiet_window_after_a_message(clock: FakeClock) -> None:
    d = Debounce(clock)
    assert not d
    d.add(user_text("hey"))
    assert d and d.delay() == 1.5


def test_typing_extends_from_the_last_message(clock: FakeClock) -> None:
    d = Debounce(clock)
    d.add(user_text("i think"))
    d.typing = True
    assert d.delay() == 5.0
    clock.advance(3)
    assert d.delay() == 2.0
    d.typing = False
    assert d.delay() == 1.5


def test_caps_on_time_and_count(clock: FakeClock) -> None:
    d = Debounce(clock)
    d.add(user_text("a"))
    clock.advance(7)
    d.add(user_text("b"))
    assert d.delay() == 1.0  # 8.0 cap from the first message
    for n in range(4):
        d.add(user_text(str(n)))
    assert d.delay() == 0.0  # six buffered


def test_take_empties_and_resets(clock: FakeClock) -> None:
    d = Debounce(clock)
    d.add(user_text("a"))
    clock.advance(7)
    taken = d.take()
    assert len(taken) == 1 and not d
    d.add(user_text("b"))
    assert d.delay() == 1.5  # the cap restarts with the new first message
