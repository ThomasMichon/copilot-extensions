"""Sync remains available for current pairs and reports partial failures."""

from types import SimpleNamespace
import json
import os
import subprocess
import sys

import pytest

from worktree_manager.production_picker.picker_tui import derive, engine_pivot_actions, maintenance


@pytest.mark.parametrize("paired,live,eligible", [
    (True, False, True), (False, False, False), (True, True, False),
])
def test_current_pair_sync_reaches_the_affirmative_dialog(monkeypatch, paired, live, eligible):
    row = derive.norm({
        "id": "fixture-wt", "status": "active", "state": "unused",
        "dirty": 0, "ahead": 0, "behind": 0, "ff_eligible": False,
        "pair_id": "pair" if paired else None, "pair_role": "harness",
        "session_bound_live": live, "turn_count": 4,
    }, "fixture-machine", "Win")
    dialogs = []
    monkeypatch.setattr(engine_pivot_actions, "ScopeDlgScreen", lambda dialog, *_args: dialog)
    screen = SimpleNamespace(
        _scope_label=lambda: "local", _scope_data=lambda: [row],
        _record_supports=lambda *_args: True, _row_key=lambda _row: "row",
        _impact_rows=lambda *_args: [], app=SimpleNamespace(
            push_screen=lambda dialog, *_args: dialogs.append(dialog),
        ),
    )
    engine_pivot_actions.PickerScreenPivotActionsMixin._open_sync(screen)
    assert dialogs[0]["opts"][0]["ids"] == ({"row"} if eligible else set())
    assert "no software install" in dialogs[0]["prompt"]


def test_partial_pair_progress_is_not_a_successful_sync():
    assert not maintenance._result_ok("sync", {
        "updated": True, "ok": False, "reason": "pair-sync-blocked",
        "pair_results": [{"updated": True}, {"updated": False, "reason": "dirty"}],
    })
    assert maintenance._result_ok("sync", {"ok": True, "reason": "up-to-date"})
    assert not maintenance._result_ok("sync", {"ok": "false", "updated": True})


def test_prior_receiver_refuses_pair_sync_before_checkout_effects(tmp_path, monkeypatch):
    effect = tmp_path / "source-was-mutated"
    source = (
        "import argparse,json,sys\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command',required=True)\n"
        "sync=s.add_parser('sync'); sync.add_argument('--worktree-id'); sync.add_argument('--json',action='store_true')\n"
        "p.parse_args()\n"
        f"Path({str(effect)!r}).touch()\n"
        "print(json.dumps({'updated':True}))\n"
    )
    calls = []

    def run_json(project, arguments, **kwargs):
        calls.append(arguments)
        result = subprocess.run(
            [sys.executable, "-c", source, *arguments],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return json.loads(result.stdout) if result.returncode == 0 else {
            "ok": False, "reason": result.stderr,
        }

    monkeypatch.setattr(maintenance.engine_client, "run_json", run_json)
    result = maintenance._make_task(
        "sync", "fixture-wt", "fixture-machine", "Win", True, project="fixture",
    )()
    assert not effect.exists() and len(calls) == 1
    assert not maintenance._result_ok("sync", result)
    assert "unrecognized arguments: --paired" in result["reason"]


@pytest.mark.parametrize("result,expected", [
    ({"ok": False, "reason": "pair-sync-blocked", "detail": "knowledge: dirty"},
     ["pair-sync-blocked", "knowledge: dirty"]),
    ({"ok": False, "reason": "usage: sync\nerror: unrecognized arguments: --paired"},
     ["unrecognized arguments: --paired"]),
    ({"ok": False, "reason": "pair-sync-blocked", "detail": "knowledge: ff-failed",
      "pair_results": [{"role": "harness", "updated": True, "reason": "updated"},
                       {"role": "knowledge", "updated": False, "reason": "ff-failed"}]},
     ["harness: updated", "knowledge: ff-failed"]),
    ({"ok": False, "reason": "Sync ended without a complete terminal result", "outcome": "unknown",
      "pair_results": [{"role": "harness", "updated": True, "reason": "updated"}]},
     ["Sync outcome unknown; no automatic replay.", "harness: updated"]),
])
def test_actual_progress_panel_preserves_sync_cause_and_partial_progress(result, expected):
    from worktree_manager.production_picker.picker_tui.engine_live_screens import ProgressScreen
    from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import PickerScreenMaintenanceActionsMixin

    executor = maintenance.MaintenanceExecutor("sync", [("row", lambda: result)])
    executor._run()
    screen = SimpleNamespace(
        executor=executor, spin=lambda: "*",
        progress={"op": "sync", "verb": "Sync", "scope": "fixture",
                  "done": False, "armed": True,
                  "items": [{"key": "row", "id4": "fixture", "title": "Task", "state": "pending"}]},
    )
    PickerScreenMaintenanceActionsMixin._poll_executor(screen)
    text = ProgressScreen(screen)._panel().renderable.plain
    assert screen.progress["done"] and screen.progress["items"][0]["state"] == "failed"
    assert all(value in text for value in expected)


def test_finished_sync_can_browse_every_failure_and_partial_result():
    from worktree_manager.production_picker.picker_tui.engine_input import PickerScreenInputMixin
    from worktree_manager.production_picker.picker_tui.engine_live_screens import ProgressScreen
    from worktree_manager.production_picker.picker_tui.styles import canonical_key

    screen = SimpleNamespace(
        spin=lambda: "*",
        progress={"op": "sync", "verb": "Sync", "scope": "fixture",
                  "done": True, "armed": True, "items": [
                      {"id4": str(index), "title": f"Task {index}", "state": "failed",
                       "result": {"ok": False, "reason": f"failure-{index}",
                                  "pair_results": [{"role": "harness", "reason": "updated",
                                                    "updated": True}]}}
                      for index in range(4)
                  ]},
    )
    panel = ProgressScreen(screen)
    text = panel._panel().renderable.plain
    assert "failure-0" in text and "failure-3" not in text
    assert "browse results" in text
    for key in ("down", "pagedown", "end", "up", "pageup", "home"):
        PickerScreenInputMixin._key_progress(screen, canonical_key(key))
        text = panel._panel().renderable.plain
        expected = 0 if key in ("up", "pageup", "home") else 1
        assert screen.progress["result_top"] == expected
        assert f"failure-{3 if expected else 0}" in text
        assert "harness: updated" in text
    assert screen.progress["done"]
