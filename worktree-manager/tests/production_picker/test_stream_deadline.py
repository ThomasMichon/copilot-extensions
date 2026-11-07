"""Progress renews communication, never process lifetime or released custody."""

import json
from types import SimpleNamespace

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


@pytest.mark.parametrize("publication", ["row", "delta", "removed", "summary"])
def test_publication_fences_old_expiry_before_releasing_its_lock(
    timers, monkeypatch, publication,
):
    from worktree_manager.production_picker.picker_tui import pivots, tasks

    reg = pivots.parse_manifest({
        "label": "Fixture", "list": ["provider", "list"], "stream": True,
    }, name="fixture", source_path="fixture")
    runtime = tasks.RegisteredPivotRuntime(reg)
    expired, retirements = [], []
    events = [{"type": "row", "entry": {"id": "one", "title": "Original"}}]
    if publication != "row":
        events.append({
            "type": publication, "id": "one",
            "entry": {"id": "one", "title": "Updated"},
            "summary": {"status": "checking"},
        })

    def frames():
        for event in events:
            if event["type"] == publication:
                expired.append(timers[0 if publication == "row" else -1].callback)
            yield json.dumps(event) + "\n"
        yield '{"type":"done"}\n'

    proc = SimpleNamespace(
        stdout=frames(), returncode=0, communicate=lambda **_kwargs: ("", ""),
    )
    monkeypatch.setattr(tasks, "_resolve_command", lambda argv: argv)
    monkeypatch.setattr(tasks, "_kill_proc_tree", lambda proc: retirements.append(proc))
    monkeypatch.setattr(runtime, "_spawn_stream", lambda _argv: proc)
    monkeypatch.setattr(runtime, "_untrack", lambda _proc: None)
    monkeypatch.setattr(
        runtime, "_release_startup", lambda _scope: expired.pop()() if expired else None,
    )
    try:
        runtime._run_list_stream(None, {})
        assert not retirements
        assert runtime.get(None)[0] == "ready"
        assert runtime.get_summary(None) == (
            {"status": "checking"} if publication == "summary" else {}
        )
    finally:
        runtime.close()
