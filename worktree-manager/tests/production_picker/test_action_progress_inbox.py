"""Registered action readers publish data; only their modal applies it."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from textual.app import App
from textual.events import Key

from worktree_manager.production_picker.picker_tui.engine_input import PickerScreenInputMixin
from worktree_manager.production_picker.picker_tui.engine_live_screens import ProgressScreen
from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
    PickerScreenMaintenanceActionsMixin,
)
from worktree_manager.production_picker.picker_tui.engine_pivot_actions import (
    PickerScreenPivotActionsMixin,
)
from worktree_manager.production_picker.picker_tui.inbox import InboxUpdated


class _Runtime:
    def __init__(self, outcome=(True, "finished"), refresh_error=None):
        self.outcome = outcome
        self.refresh_error = refresh_error
        self.ready = threading.Event()
        self.release = threading.Event()
        self.thread = None
        self.cancel = None
        self.calls = []

    def run_action_stream(self, action, ctx, on_frame, should_cancel):
        self.thread = threading.current_thread()
        self.cancel = should_cancel
        on_frame(-10, "first")
        on_frame(140, "")
        on_frame(None, "latest")
        self.ready.set()
        assert self.release.wait(5), "test must release its reader"
        on_frame(25, "late frame")
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def invalidate(self, scope):
        self.calls.append(("invalidate", scope))

    def ensure(self, scope):
        self.calls.append(("ensure", scope))
        if self.refresh_error is not None:
            raise self.refresh_error

    def finish(self):
        self.release.set()
        if self.thread is not None:
            self.thread.join(5)
            assert not self.thread.is_alive()


class _Engine(
    PickerScreenPivotActionsMixin, PickerScreenMaintenanceActionsMixin, PickerScreenInputMixin,
):
    def __init__(self, runtime):
        self.runtime = runtime
        self.progress = None
        self.debug = ""
        self.sel = ("T", 0)
        self.modal = None
        self.app = SimpleNamespace(push_screen=self._push)

    def _push(self, modal):
        self.modal = modal

    def _pivot_runtime(self, reg):
        return self.runtime

    def start(self):
        self._run_task_action_progress(
            SimpleNamespace(), SimpleNamespace(label="Recycle"),
            {"machine": "local", "_manager_refresh_scopes": ("origin",)}, "row",
        )
        return self.modal

    def stops(self):
        return [self.sel]

    def spin(self):
        return "."

    def _advance_progress(self):
        assert self.progress["kind"] == "action-stream"


@pytest.fixture
def action_run():
    runtime = _Runtime()
    engine = _Engine(runtime)
    yield engine, runtime
    runtime.finish()


def test_reader_snapshots_are_private_until_nondestructive_modal_consumption(action_run):
    engine, runtime = action_run
    modal = engine.start()
    assert runtime.ready.wait(5)
    progress = engine.progress
    assert progress["pct"] is None
    assert progress["msg"] == "starting…"
    first = modal.inbox.peek("action-progress")
    assert first["pct"] == 100
    assert first["msg"] == "latest"
    assert modal.inbox.peek("action-progress") is first

    for _ in range(2):
        text = modal._panel().renderable.plain
        assert "latest" in text and "100.0%" in text and "Cancel" in text
    assert progress["pct"] == 100 and not progress["done"]

    runtime.finish()
    assert not progress["done"]
    assert first["msg"] == "latest" and not first["done"]
    text = modal._panel().renderable.plain
    assert progress["done"] and not progress["error"]
    assert "finished" in text and "Close" in text
    assert modal.inbox.peek("action-progress")["msg"] == "finished"
    assert runtime.calls == [("invalidate", "origin"), ("ensure", "origin")]


@pytest.mark.parametrize("outcome,refresh_error,message", [
    ((False, "provider rejected"), None, "provider rejected"),
    (RuntimeError("reader broke"), None, "reader broke"),
    ((True, "finished"), RuntimeError("refresh broke"), "refresh broke"),
    ((False, "provider rejected"), RuntimeError("refresh broke"),
     "provider rejected; refresh failed: refresh broke"),
])
def test_reader_errors_settle_through_the_actual_modal(outcome, refresh_error, message):
    runtime = _Runtime(outcome, refresh_error)
    engine = _Engine(runtime)
    try:
        modal = engine.start()
        assert runtime.ready.wait(5)
        runtime.finish()
        text = modal._panel().renderable.plain
        assert engine.progress["done"] and engine.progress["error"] == message
        assert "failed" in text and "Close" in text
        assert runtime.calls == [("invalidate", "origin"), ("ensure", "origin")]
    finally:
        runtime.finish()


@pytest.mark.parametrize("raises", [False, True])
def test_early_completion_survives_an_undeliverable_wake(action_run, monkeypatch, raises):
    engine, runtime = action_run
    attempted = []

    def post_message(self, message):
        attempted.append(message)
        if raises:
            raise RuntimeError("closing pump")
        return False

    monkeypatch.setattr(ProgressScreen, "post_message", post_message)
    modal = engine.start()
    assert runtime.ready.wait(5)
    runtime.finish()
    assert attempted
    assert not engine.progress["done"]
    assert "finished" in modal._panel().renderable.plain
    assert engine.progress["done"]
    assert "finished" in modal._panel().renderable.plain


@pytest.mark.parametrize("old_outcome", [(True, "old finished"), (False, "old error")])
def test_old_run_frames_wake_tick_and_input_cannot_change_or_cancel_new_run(old_outcome):
    old = _Runtime(old_outcome)
    new = _Runtime()
    engine = _Engine(old)

    async def run():
        app = App()
        engine.app = app
        async with app.run_test() as pilot:
            engine.start()
            first = app.screen
            assert old.ready.wait(5)
            await pilot.press("escape")
            assert engine.progress is None and old.cancel()
            engine.runtime = new
            engine.start()
            second = app.screen
            progress = engine.progress
            assert new.ready.wait(5)
            await pilot.pause()
            assert "latest" in second._panel().renderable.plain

            old.finish()
            first._on_tick()
            first.on_key(Key("escape", None))
            first.on_inbox_updated(InboxUpdated())
            await pilot.pause()
            assert app.screen is second
            assert engine.progress is progress and not progress["done"]
            assert progress["msg"] == "latest" and not new.cancel()

            new.finish()
            # Input consumes a terminal snapshot even before another render tick.
            second.on_key(Key("enter", None))
            await pilot.pause()
            assert engine.progress is None and not new.cancel()
            assert "Recycle done" in engine.debug

    try:
        asyncio.run(run())
    finally:
        old.finish()
        new.finish()
