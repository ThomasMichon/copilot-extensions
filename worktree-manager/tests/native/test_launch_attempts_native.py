"""Explicit process/ACL/latency lane; not a required-CI process matrix."""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import sqlite3
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


@pytest.mark.parametrize("boundary", ["resolve", "launch"])
def test_manager_crash_leaves_boundary_evidence_without_invented_completion(tmp_path, boundary):
    attempt_id = uuid4().hex
    result = _run(_WRITER, str(tmp_path), attempt_id, "after-commit")
    assert result.returncode == 71, result.stderr
    script = """
import os, sys
from worktree_manager import __main__ as main, engine_client, launcher
from worktree_manager.picker_app import LaunchRequest
root, attempt_id, boundary = sys.argv[1:]
os.environ["WORKTREE_MANAGER_ROOT"] = root
main._relocated_launch_script = lambda: None
launcher.uses_ahp = lambda *a: False
def resolve(*a, **kw):
    if boundary == "resolve":
        os._exit(73)
    return engine_client.launch_plan_from_dict({
        "action": "exec", "cmd": ["fixture"], "worktree_id": "worktree-fixture",
    })
engine_client.resolve_launch_plan = resolve
launcher.launch = lambda *a, **kw: os._exit(73)
main._run_launch(LaunchRequest(
    project="example", worktree_id=None, mode="new", attempt_id=attempt_id,
))
"""
    result = _run(script, str(tmp_path), attempt_id, boundary)
    assert result.returncode == 73, result.stderr
    reader = """
import json, pathlib, sys
from worktree_manager.launch_attempts import LaunchAttempts
detail = LaunchAttempts(pathlib.Path(sys.argv[1])).detail(sys.argv[2], include_input=True)
assert detail["input"]["prompt"] == "  native exact text\\nsecond line\\n"
print(json.dumps({"phases": [e["phase"] for e in detail["events"]],
                  "attempt_id": detail["attempt_id"], "exact_input": True}))
"""
    result = _run(reader, str(tmp_path), attempt_id)
    assert result.returncode == 0, result.stderr
    expected = ["confirmed", "resolution-requested"]
    if boundary == "launch":
        expected += ["resolved", "launch-requested"]
    assert json.loads(result.stdout) == {
        "phases": expected, "attempt_id": attempt_id, "exact_input": True,
    }


@pytest.mark.parametrize("boundary", ["before-first-event", "after-commit"])
def test_v1_upgrade_crash_retains_original_and_never_leaves_partial_unknown_intent(
    tmp_path, boundary,
):
    store = journal.LaunchAttempts(tmp_path)
    original_id, upgrade_id = uuid4().hex, uuid4().hex
    store.record_intent(
        original_id, action="open", project="example", surface="native", is_local=True,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA user_version=1")
    original = store.detail(original_id, include_input=True)
    writer = _WRITER.replace("is_local=True", "is_local=None")
    result = _run(writer, str(tmp_path), upgrade_id, boundary)
    assert result.returncode == 71, result.stderr
    if boundary == "before-first-event":
        recovery_id = uuid4().hex
        store.record_intent(
            recovery_id, action="open", project="example", surface="native", is_local=None,
        )
        assert {row["attempt_id"] for row in store.entries()} == {original_id, recovery_id}
    else:
        reader = _READER.replace(
            'detail = store.detail(sys.argv[2], include_input=True)',
            'detail = store.detail(sys.argv[2], include_input=True)\nassert detail["is_local"] is None',
        )
        result = _run(reader, str(tmp_path), upgrade_id)
        assert result.returncode == 0, result.stderr
        assert {row["attempt_id"] for row in store.entries()} == {original_id, upgrade_id}
    assert store.detail(original_id, include_input=True) == original
    assert int.from_bytes(store.path.read_bytes()[60:64], "big") == 2


_UI_WRITER = """
import asyncio, os, pathlib, sys
from uuid import UUID
from textual.app import App
from types import SimpleNamespace
from worktree_manager import launch_confirmation
from worktree_manager.launch_attempts import LaunchAttempts
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.engine_dialogs import SubMenuScreen
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
    engine = SimpleNamespace(
        real_ops=True, src=SimpleNamespace(), _run_bg=lambda *a, **kw: None,
        _resume_decision=lambda rec, **options: {
            "action": "resume", "is_local": True, "worktree_id": "native-worktree",
            "options": options,
        },
    )
    dialog = SubMenuScreen(
        {"is_local": True, "raw": {"id": "native-worktree"}}, ["Resume"], engine=engine,
    )
    dialog._no_mux = True
    async with app.run_test() as pilot:
        app.push_screen(dialog, lambda _: pathlib.Path(root, "effect").write_text("launched"))
        await pilot.pause()
        dialog.query_one(SeedPromptField).load_text("  native exact text\\nsecond line\\n")
        dialog.on_option_list_option_selected(SimpleNamespace(option_index=0))
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
assert detail["surface"] == "worktree-actions"
assert detail["input"]["prompt"] == "  native exact text\\nsecond line\\n"
assert detail["input"]["options"] == {"no_mux": True, "ahp": False, "title": None}
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
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui.engine_dialogs import SubMenuScreen

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
                engine = SimpleNamespace(
                    real_ops=True, src=SimpleNamespace(), _run_bg=lambda *a, **kw: None,
                    _resume_decision=lambda rec, **options: {
                        "action": "resume", "is_local": True, "worktree_id": "native-worktree",
                        "options": options,
                    },
                )
                dialog = SubMenuScreen(
                    {"is_local": True, "raw": {"id": "native-worktree"}}, ["Resume"], engine=engine,
                )
                completed = []
                app.push_screen(dialog, completed.append)
                await pilot.pause()
                start = time.perf_counter_ns()
                dialog.on_option_list_option_selected(SimpleNamespace(option_index=0))
                samples.append((time.perf_counter_ns() - start) / 1_000_000)
                assert completed == [] and dialog.is_mounted
                async with asyncio.timeout(10):
                    while not completed:
                        await pilot.pause(0.01)
                assert completed == [("Resume", False, False, "")]

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


def test_manager_observation_chain_latency_is_measured_without_engine_or_launch_work(
    tmp_path, monkeypatch, capsys,
):
    from worktree_manager import __main__ as main, engine_client, launch_observations, launcher
    from worktree_manager.picker_app import LaunchRequest

    store = journal.LaunchAttempts(tmp_path)
    monkeypatch.setattr(launch_observations, "LaunchAttempts", lambda: store)
    monkeypatch.setattr(main, "_relocated_launch_script", lambda: None)
    monkeypatch.setattr(launcher, "uses_ahp", lambda *a: False)
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: engine_client.launch_plan_from_dict({
        "action": "exec", "cmd": ["fixture"], "worktree_id": "fixture-worktree",
    }))
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: 0)
    samples = []
    for _ in range(101):
        identity = uuid4().hex
        store.record_intent(
            identity, action="new", project="example", surface="native", is_local=True,
        )
        start = time.perf_counter_ns()
        assert main._run_launch(LaunchRequest(
            project="example", worktree_id=None, mode="new", attempt_id=identity,
        )) == 0
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
        assert [event["phase"] for event in store.detail(identity)["events"]] == [
            "confirmed", "resolution-requested", "resolved", "launch-requested", "launcher-returned",
        ]
    warm = sorted(samples[1:])
    disabled = []
    for _ in range(101):
        start = time.perf_counter_ns()
        assert main._run_launch(LaunchRequest(
            project="example", worktree_id=None, mode="new",
        )) == 0
        disabled.append((time.perf_counter_ns() - start) / 1_000_000)
    baseline = sorted(disabled[1:])
    report = {
        "boundary": "manager-four-observation-chain-after-confirmation",
        "platform": sys.platform, "attempts": 101, "events": 505, "failures": 0,
        "first_chain_ms": samples[0], "warm_samples_ms": samples[1:],
        "quantile": "nearest-rank", "warm_p50_ms": warm[49],
        "warm_p95_ms": warm[94], "warm_p99_ms": warm[98], "warm_max_ms": warm[-1],
        "engine_and_launcher": "synthetic-no-process-network-work",
        "disabled_first_chain_ms": disabled[0], "disabled_warm_samples_ms": disabled[1:],
        "disabled_p50_ms": baseline[49], "disabled_p95_ms": baseline[94],
        "disabled_p99_ms": baseline[98], "disabled_max_ms": baseline[-1],
        "comparison": "same-workload-sequential-101-enabled-then-101-disabled",
        "tail_qualification": "insufficient-samples",
    }
    assert all(math.isfinite(value) and value >= 0 for value in samples)
    with capsys.disabled():
        print("MANAGER_OBSERVATION_NATIVE_MEASUREMENT " + json.dumps(report))
