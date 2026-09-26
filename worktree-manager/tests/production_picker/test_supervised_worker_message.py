"""Send message to a supervised remote worker (venue-pivots-ux, #3657 step 3b)."""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
from textual.app import App
from worktree_manager.production_picker.picker_tui import engine_worker_actions as ewa
from worktree_manager.production_picker.picker_tui import pivots
from worktree_manager.production_picker.picker_tui.engine_worker_dialogs import WorkerMessageScreen


def test_message_argv_steers_by_default_and_interrupts_on_request():
    assert ewa.worker_message_argv("ab", "sid-1", "steer") == [
        "ab", "send", "sid-1", "-", "--no-wait", "--steer"]
    assert ewa.worker_message_argv("ab", "sid-1", "interrupt")[-1] == "--interrupt"
    assert ewa.worker_message_argv("ab", "sid-1", "anything-else")[-1] == "--steer"


class _Engine(ewa.PickerScreenWorkerActionsMixin):
    def __init__(self):
        self.debug = ""
        self.pushed = []
        self.bg = []
        self.app = types.SimpleNamespace(push_screen=lambda screen, cb: self.pushed.append((screen, cb)))

    def _run_bg(self, label, work, done=None, **_kw):
        self.bg.append(label)
        done(work())

    def refresh(self):
        pass


def test_send_needs_a_live_session_and_the_bridge(monkeypatch):
    ok, msg = _Engine()._send_worker_message({"id": "cs1"})
    assert not ok and "no live session" in msg
    monkeypatch.setattr(ewa.shutil, "which", lambda name: None)
    ok, msg = _Engine()._send_worker_message({"id": "cs1", "session_id": "sid-1"})
    assert not ok and "agent-bridge" in msg


def test_send_delivers_the_typed_text_on_stdin(monkeypatch):
    runs = []

    def _run(argv, **kw):
        runs.append((argv, kw.get("input")))
        return types.SimpleNamespace(returncode=0, stdout="[>] Delivered", stderr="")

    monkeypatch.setattr(ewa.shutil, "which", lambda name: "/bin/agent-bridge")
    monkeypatch.setattr(ewa.subprocess, "run", _run)
    eng = _Engine()
    ok, _ = eng._send_worker_message({"id": "cs1", "title": "cs-one", "session_id": "sid-1"})
    assert ok
    [(screen, callback)] = eng.pushed
    assert isinstance(screen, WorkerMessageScreen)
    callback(("interrupt", "stop and look at the Lists load path"))
    assert runs == [(["/bin/agent-bridge", "send", "sid-1", "-", "--no-wait", "--interrupt"],
                     "stop and look at the Lists load path")]
    assert eng.debug == "interrupted cs-one"


def test_cancelled_message_sends_nothing(monkeypatch):
    monkeypatch.setattr(ewa.shutil, "which", lambda name: "/bin/agent-bridge")
    monkeypatch.setattr(ewa.subprocess, "run", lambda *a, **k: pytest.fail("sent"))
    eng = _Engine()
    eng._send_worker_message({"id": "cs1", "session_id": "sid-1"})
    eng.pushed[0][1](None)
    assert eng.debug == "message cancelled" and eng.bg == []


def test_failed_send_reports_the_first_line(monkeypatch):
    monkeypatch.setattr(ewa.shutil, "which", lambda name: "/bin/agent-bridge")
    monkeypatch.setattr(ewa.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="[FAIL] session not found\nmore"))
    eng = _Engine()
    eng._send_worker_message({"id": "cs1", "session_id": "sid-1"})
    eng.pushed[0][1](("steer", "hello"))
    assert eng.debug == "message to cs1 failed: [FAIL] session not found"


def _dialog_result(keys, text=""):
    results = []

    class _Host(App):
        def on_mount(self):
            self.push_screen(WorkerMessageScreen("cs-one"), results.append)

    async def _drive():
        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            if text:
                app.screen.query_one("#wm-text").insert(text)
            for key in keys:
                await pilot.press(key)
            await pilot.pause()
    asyncio.run(_drive())
    return results


def test_dialog_steers_interrupts_and_cancels():
    assert _dialog_result(["ctrl+s"], "keep going") == [("steer", "keep going")]
    assert _dialog_result(["ctrl+x"], "stop") == [("interrupt", "stop")]
    assert _dialog_result(["escape"], "draft") == [None]


def test_dialog_refuses_an_empty_message():
    assert _dialog_result(["ctrl+s"]) == []


@pytest.mark.parametrize("plugin", ["agent-codespaces", "agent-containers"])
def test_venue_manifests_offer_send_message(plugin):
    repo = Path(__file__).resolve().parents[3]
    path = repo / "plugins" / plugin / "pivots" / f"{plugin}.json"
    reg = pivots.parse_manifest(json.loads(path.read_text(encoding="utf-8")),
                                name=plugin, source_path=str(path))
    assert "send-worker-message" in {a.internal for a in reg.actions if a.internal}
