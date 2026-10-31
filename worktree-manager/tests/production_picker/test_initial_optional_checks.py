"""Automatic courtesy checks yield to the first authoritative local load."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from worktree_manager import manager_update_check, resident_picker
from worktree_manager.production_picker.picker_tui import engine_runtime
from worktree_manager.production_picker.picker_tui.engine_loading import PickerScreenLoadingMixin


@pytest.mark.parametrize("poll", ["update", "manager", "orphans"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_initial_optional_work_waits_for_local_state_and_honors_exit(monkeypatch, poll, cancelled):
    waiting = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    cancel = threading.Event()
    calls, queued = [], []

    def wait(project, is_cancelled):
        waiting.set()
        assert release.wait(3)
        return not is_cancelled()

    monkeypatch.setattr(resident_picker, "wait_for_local", wait)
    monkeypatch.setattr(engine_runtime.update_stage, "indicator_state", lambda: calls.append("update"))
    monkeypatch.setattr(manager_update_check, "indicator_state", lambda: "idle")
    monkeypatch.setattr(manager_update_check, "should_check", lambda: True)
    monkeypatch.setattr(manager_update_check, "check_now", lambda: calls.append("manager"))
    screen = SimpleNamespace(
        _last_update_state_poll=0.0, _update_state_poll_pending=False,
        _bg_cancel=cancel, src=SimpleNamespace(orphans=lambda: calls.append("orphans")),
        _run_bg=lambda label, work, done, **kwargs: queued.append(work),
    )
    screen._wait_for_initial_local = lambda: engine_runtime.PickerScreenRuntimeMixin._wait_for_initial_local(screen)
    method = {
        "update": "_poll_update_state", "manager": "_poll_manager_update_state",
        "orphans": "_poll_orphan_state",
    }[poll]
    getattr(engine_runtime.PickerScreenRuntimeMixin, method)(screen)
    assert len(queued) == 1

    def run():
        try:
            queued[0]()
        finally:
            finished.set()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert waiting.wait(1)
        assert not finished.wait(0.02) and calls == []
        if cancelled:
            cancel.set()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert calls == ([] if cancelled else [poll])


def test_explicit_orphan_refresh_does_not_wait_for_initial_local_state(monkeypatch):
    calls, queued = [], []
    screen = SimpleNamespace(
        src=SimpleNamespace(orphans=lambda: calls.append("orphans")),
        _run_bg=lambda label, work, done, **kwargs: queued.append(work),
        _wait_for_initial_local=lambda: pytest.fail("explicit refresh was delayed"),
    )
    engine_runtime.PickerScreenRuntimeMixin._poll_orphan_state(screen, force=True)
    queued[0]()
    assert calls == ["orphans"]


@pytest.mark.parametrize("outcome", ["complete", "failed", "cancelled"])
def test_local_settlement_waits_for_the_completed_visible_refresh(monkeypatch, outcome):
    callbacks, released = [], []
    state = "loading"
    authoritative = set()
    cancel = threading.Event()
    loader = SimpleNamespace(
        state_for_source=lambda source_id: state,
        authoritative_source_ids=lambda: authoritative,
    )
    screen = SimpleNamespace(
        loader=loader, source_tabs=[{"local": True, "source_id": "local-source"}],
        _bg_cancel=cancel, call_after_refresh=callbacks.append,
    )
    monkeypatch.setattr(resident_picker, "settle_local", lambda: released.append(True))
    settle = lambda: PickerScreenLoadingMixin._settle_initial_local_after_refresh(screen)
    settle()
    assert callbacks == []
    state = "ready"
    settle()
    assert callbacks == []  # A connectivity ping or incomplete prefix is not current.
    if outcome == "failed":
        state = "failed"
    else:
        authoritative.add("local-source")
    settle()
    settle()
    assert len(callbacks) == 1 and released == []
    if outcome == "cancelled":
        cancel.set()
    callbacks[0]()
    assert released == ([] if outcome == "cancelled" else [True])
