"""Explicit process/ACL/latency lane; not a required-CI process matrix."""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import launch_attempts as journal


_WRITER = """
import os, pathlib, sqlite3, sys
from worktree_manager.launch_attempts import LaunchAttempts
root, attempt_id, boundary = sys.argv[1:]
if boundary == "before-first-event":
    connect = sqlite3.connect
    class Interrupted(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.startswith("INSERT INTO events"):
                os._exit(71)
            return super().execute(sql, parameters)
    sqlite3.connect = lambda *a, **kw: connect(*a, **kw, factory=Interrupted)
store = LaunchAttempts(pathlib.Path(root))
store.record_intent(attempt_id, action="new", project="example", surface="native",
                    is_local=True, prompt="  native exact text\\nsecond line\\n",
                    options={"no_mux": True})
os._exit(71)
"""

_READER = """
import json, pathlib, sys
from worktree_manager.launch_attempts import LaunchAttempts
store = LaunchAttempts(pathlib.Path(sys.argv[1]))
detail = store.detail(sys.argv[2], include_input=True)
assert detail["input"]["prompt"] == "  native exact text\\nsecond line\\n"
assert detail["input"]["options"] == {"no_mux": True}
assert [event["phase"] for event in detail["events"]] == ["confirmed"]
print(json.dumps({"attempt_id": detail["attempt_id"], "exact_input": True,
                  "confirmed_events": len(detail["events"])}))
"""


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        **no_window_kwargs(),
    )


def test_committed_intent_survives_abrupt_process_exit_and_separate_reader(tmp_path):
    attempt_id = uuid4().hex
    result = _run(_WRITER, str(tmp_path), attempt_id, "after-commit")
    assert result.returncode == 71, result.stderr
    result = _run(_READER, str(tmp_path), attempt_id)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "attempt_id": attempt_id, "exact_input": True, "confirmed_events": 1,
    }


def test_crash_between_intent_and_first_event_does_not_leave_partial_confirmation(tmp_path):
    attempt_id = uuid4().hex
    result = _run(_WRITER, str(tmp_path), attempt_id, "before-first-event")
    assert result.returncode == 71, result.stderr
    store = journal.LaunchAttempts(tmp_path)
    recovery_id = uuid4().hex
    store.record_intent(
        recovery_id, action="open", project="example", surface="native", is_local=True,
    )
    assert [row["attempt_id"] for row in store.entries()] == [recovery_id]


_UI_WRITER = """
import asyncio, os, pathlib, sys
from uuid import UUID
from textual.app import App
from worktree_manager import launch_confirmation
from worktree_manager.launch_attempts import LaunchAttempts
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.engine_prompt_dialog import PromptDlgScreen
from worktree_manager.production_picker.picker_tui.engine_focus import FocusGroup
from worktree_manager.production_picker.picker_tui.seed_prompt_field import SeedPromptField
root, attempt_id, boundary = sys.argv[1:]
os.environ["WORKTREE_MANAGER_ROOT"] = root
context.set_project("example")
launch_confirmation.uuid4 = lambda: UUID(attempt_id)
record = LaunchAttempts.record_intent
def interrupted(store, ident, **intent):
    if boundary == "before-save":
        os._exit(72)
    record(store, ident, **intent)
    os._exit(72)
LaunchAttempts.record_intent = interrupted
async def run():
    app = App()
    dialog = PromptDlgScreen("Resume", "Prompt", launch_decision={
        "action": "resume", "is_local": True, "worktree_id": "native-worktree",
        "options": {"no_mux": True},
    })
    async with app.run_test() as pilot:
        app.push_screen(dialog, lambda _: pathlib.Path(root, "effect").write_text("launched"))
        await pilot.pause()
        dialog.query_one(SeedPromptField).load_text("  native exact text\\nsecond line\\n")
        dialog.on_focus_group_activated(
            FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm"))
        await asyncio.sleep(15)
asyncio.run(run())
raise SystemExit("confirmation worker never reached the selected crash boundary")
"""

_UI_READER = """
import json, pathlib, sys
from worktree_manager.launch_attempts import LaunchAttempts
store = LaunchAttempts(pathlib.Path(sys.argv[1]))
detail = store.detail(sys.argv[2], include_input=True)
assert detail["action"] == "resume" and detail["worktree_id"] == "native-worktree"
assert detail["surface"] == "resume-prompt"
assert detail["input"]["prompt"] == "  native exact text\\nsecond line\\n"
assert detail["input"]["options"] == {"no_mux": True, "title": None}
assert [event["phase"] for event in detail["events"]] == ["confirmed"]
assert not pathlib.Path(sys.argv[1], "effect").exists()
print(json.dumps({"exact_input": True, "launch_effect": False}))
"""


@pytest.mark.parametrize("boundary", ["before-save", "after-save-before-dismiss"])
def test_native_ui_crash_boundary_never_replays_or_dismisses_early(tmp_path, boundary):
    attempt_id = uuid4().hex
    result = _run(_UI_WRITER, str(tmp_path), attempt_id, boundary)
    assert result.returncode == 72, result.stderr
    assert not (tmp_path / "effect").exists()
    if boundary == "before-save":
        assert journal.LaunchAttempts(tmp_path).entries() == []
    else:
        result = _run(_UI_READER, str(tmp_path), attempt_id)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"exact_input": True, "launch_effect": False}


def test_native_confirmation_handler_acknowledges_before_slow_storage(
    tmp_path, monkeypatch, capsys,
):
    from textual.app import App
    from worktree_manager.production_picker import context
    from worktree_manager.production_picker.picker_tui.engine_focus import FocusGroup
    from worktree_manager.production_picker.picker_tui.engine_prompt_dialog import PromptDlgScreen

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("example")
    record = journal.LaunchAttempts.record_intent

    def slow(store, attempt_id, **intent):
        time.sleep(0.2)
        record(store, attempt_id, **intent)

    monkeypatch.setattr(journal.LaunchAttempts, "record_intent", slow)
    samples = []

    async def run():
        app = App()
        async with app.run_test() as pilot:
            for _ in range(20):
                dialog = PromptDlgScreen("Resume", "Prompt")
                completed = []
                app.push_screen(dialog, completed.append)
                await pilot.pause()
                start = time.perf_counter_ns()
                dialog.on_focus_group_activated(
                    FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm"),
                )
                samples.append((time.perf_counter_ns() - start) / 1_000_000)
                assert completed == [] and dialog.is_mounted
                async with asyncio.timeout(10):
                    while not completed:
                        await pilot.pause(0.01)
                assert completed == [True]

    try:
        asyncio.run(run())
    finally:
        context.reset()
    assert len(journal.LaunchAttempts(tmp_path).entries()) == 20
    ordered = sorted(samples)
    report = {
        "boundary": "confirmation-handler-not-end-to-end-key-or-storage",
        "platform": sys.platform, "samples_ms": samples, "count": len(samples),
        "p50_ms": ordered[9], "p95_ms": ordered[18], "p99_ms": ordered[19],
        "max_ms": ordered[-1], "failures": 0,
        "storage_delay_ms": 200, "handler_budget_ms": 100,
    }
    with capsys.disabled():
        print("LAUNCH_CONFIRMATION_NATIVE_MEASUREMENT " + json.dumps(report))
    assert ordered[-1] < 100


@pytest.mark.skipif(os.name != "nt", reason="Native Windows DACL contract")
def test_journal_and_database_have_protected_owner_system_acls(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable for native DACL inspection")
    store = journal.LaunchAttempts(tmp_path)
    store.record_intent(
        uuid4().hex, action="open", project="example", surface="native", is_local=True,
    )
    script = (
        "$ErrorActionPreference='Stop';"
        "Import-Module (Join-Path $PSHOME "
        "'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');"
        "$paths=@(" + ",".join(
            "'" + str(path).replace("'", "''") + "'" for path in (store.path.parent, store.path)
        ) + ");"
        "foreach($path in $paths){"
        "$acl=Get-Acl -LiteralPath $path;"
        "if(-not $acl.AreAccessRulesProtected){throw 'Unprotected DACL'};"
        "$sids=@($acl.Access | ForEach-Object {"
        "$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value});"
        "if(($sids | Sort-Object -Unique) -join ',' -ne 'S-1-3-4,S-1-5-18'){"
        "throw 'Unexpected DACL principal'}};"
        "Write-Output 'owner-system-only'"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "owner-system-only"


def test_native_commit_latency_samples_are_complete_and_reported(tmp_path, capsys):
    store = journal.LaunchAttempts(tmp_path)
    samples = []
    for index in range(101):
        start = time.perf_counter_ns()
        store.record_intent(
            uuid4().hex, action="new", project="example", surface="native",
            is_local=True, prompt="sample\n" * 100, options={"no_mux": bool(index % 2)},
        )
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    warm = sorted(samples[1:])
    report = {
        "boundary": "in-process-required-commit-not-ui-gesture",
        "platform": sys.platform, "records": 101, "failures": 0,
        "cold_ms": samples[0], "warm_samples_ms": samples[1:],
        "quantile": "nearest-rank", "warm_p50_ms": warm[49],
        "warm_p95_ms": warm[94], "warm_p99_ms": warm[98], "warm_max_ms": warm[-1],
        "tail_qualification": "insufficient-samples",
    }
    assert all(math.isfinite(value) and value >= 0 for value in samples)
    assert len(store.entries(limit=1000)) == 101
    # Measurements, not an invented latency threshold or a UI responsiveness claim.
    with capsys.disabled():
        print("LAUNCH_JOURNAL_NATIVE_MEASUREMENT " + json.dumps(report))
