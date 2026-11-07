"""Progress renews communication, never process lifetime or released custody."""

import pytest

from worktree_manager.production_picker.picker_tui import stream_deadline

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.contract("worktree_manager.stream_deadline"),
]


@pytest.fixture
def timers(monkeypatch):
    created = []

    class Timer:
        def __init__(self, _interval, callback):
            self.callback = callback
            created.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(stream_deadline.threading, "Timer", Timer)
    return created


@pytest.mark.parametrize("clock", ["silence", "process"])
def test_finite_progress_fences_old_expiry_without_clearing_either_bound(timers, clock):
    retired = []
    deadline = stream_deadline.StreamDeadline(
        subscribe=False, timeout=1, process_timeout=10, retire=lambda: retired.append(True),
    )
    deadline.commit()
    deadline.progress()
    timers[0].callback()
    assert retired == [] and not deadline.timed_out.is_set()
    timers[2 if clock == "silence" else 1].callback()
    assert retired == [True] and deadline.timed_out.is_set()
    assert deadline.process_timed_out.is_set() is (clock == "process")
    deadline.close()


def test_closed_stream_cannot_retire_a_later_consumer(timers):
    retired = []
    deadline = stream_deadline.StreamDeadline(
        subscribe=False, timeout=1, process_timeout=10, retire=lambda: retired.append(True),
    )
    deadline.progress()
    deadline.close()
    for timer in timers:
        timer.callback()
    assert retired == [] and not deadline.timed_out.is_set()
