"""Optional Sync negotiation, incremental observation and terminal outcomes."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from worktree_manager import engine_client, engine_sync
from worktree_manager.production_picker.picker_tui import maintenance
from test_repository_authentication import selected as selected


def _descriptor(tmp_path, version=1):
    slot = tmp_path / "slot"
    python = slot / ("Scripts" if os.name == "nt" else "bin") / "python"
    package = slot / ("Lib" if os.name == "nt" else "lib/python3.13")
    path = package / "site-packages/agent_worktrees/sync-progress-v1.json"
    path.parent.mkdir(parents=True)
    data = {
        "contract": "agent-worktrees.sync-progress",
        "version": version,
        "envelope": "d4",
        "operations": ["sync"],
        "options": ["--json", "--stream"],
        "terminal_result": True,
    }
    path.write_text(json.dumps(data))
    return [str(python), "-X", "pycache_prefix=cache", "-m", "agent_worktrees"], path


@pytest.mark.parametrize("version", [1, True, 0, 2, "1"])
def test_only_selected_supported_descriptor_enables_progress(tmp_path, version):
    command, path = _descriptor(tmp_path, version)
    assert engine_sync.supports_progress(command) is (type(version) is int and version == 1)
    assert not engine_sync.supports_progress(["agent-worktrees"])
    assert not engine_sync.supports_progress([sys.executable, "-c", "pass"])
    path.write_text("invalid")
    assert not engine_sync.supports_progress(command)
    path.unlink()
    assert not engine_sync.supports_progress(command)


def test_selection_and_rollback_keep_one_pinned_baseline_invocation(tmp_path, monkeypatch):
    command, path = _descriptor(tmp_path)
    path.unlink()
    calls = []
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: command)
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda project, args, **kw: calls.append((project, args, kw)) or {"ok": True},
    )
    assert engine_sync.run("demo", "selected") == {"ok": True}
    assert calls == [
        (
            "demo",
            ["sync", "--paired", "--worktree-id", "selected", "--json"],
            {"timeout": 120, "allow_nonzero": True, "engine_command": command},
        )
    ]
    other, _ = _descriptor(tmp_path / "new-selection")
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: other)
    # A supported generation is inspected afresh, never the older selection.
    monkeypatch.setattr(
        engine_sync.engine_process,
        "run",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("controlled launch refusal")),
    )
    result = engine_sync.run("demo", "selected")
    assert result["outcome"] == "unknown" and len(calls) == 1


def test_marker_selection_and_supported_rollback_requalify_the_executing_slot(
    selected, monkeypatch
):
    root, slot, authentication_descriptor = selected
    descriptor = authentication_descriptor.with_name("sync-progress-v1.json")
    descriptor.write_text(
        json.dumps(
            {
                "contract": "agent-worktrees.sync-progress",
                "version": 1,
                "envelope": "d4",
                "operations": ["sync"],
                "options": ["--json", "--stream"],
                "terminal_result": True,
            }
        )
    )
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        kwargs["stdout"].write((_done() + "\n").encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(engine_sync.engine_process, "run", run)
    assert engine_sync.run("demo", "selected")["ok"]
    assert str(slot) in calls[0][0] and calls[0][-1] == "--stream"
    old = root / "versions" / "1.2.2"
    python = old / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture prior interpreter")
    (old / ".install-complete.json").write_text("{}")
    (root / "current-version").write_text(old.name)
    baseline = []
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda _p, args, **kw: baseline.append((args, kw["engine_command"])) or {"ok": True},
    )
    assert engine_sync.run("demo", "selected")["ok"]
    assert len(calls) == 1 and len(baseline) == 1
    assert "--stream" not in baseline[0][0] and baseline[0][1][0] == str(python)
    (root / "current-version").write_text(slot.name)
    assert engine_sync.run("demo", "selected")["ok"] and len(calls) == 2


def _provider(monkeypatch, body):
    command = [
        sys.executable,
        "-B",
        "-c",
        "import json,sys,time\nfrom pathlib import Path\n" + body,
    ]
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: command)
    monkeypatch.setattr(engine_sync, "supports_progress", lambda selected: selected == command)


def _done(result=None, kind="done"):
    return json.dumps({"type": kind, "result": result or {"ok": True, "reason": "up-to-date"}})


def test_real_incremental_executor_and_progress_screen_before_process_completion(
    tmp_path, monkeypatch
):
    from worktree_manager.production_picker.picker_tui.engine_live_screens import ProgressScreen
    from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
        PickerScreenMaintenanceActionsMixin,
    )
    from worktree_manager.production_picker.picker_tui.engine_input import PickerScreenInputMixin

    release = tmp_path / "release"
    complete = {
        "ok": False,
        "reason": "pair-sync-blocked",
        "detail": "knowledge: dirty",
        "plugins": "unchanged",
        "pair_results": [
            {"role": "harness", "reason": "updated", "updated": True},
            {"role": "knowledge", "reason": "dirty", "updated": False},
        ],
    }
    _provider(
        monkeypatch,
        "print(json.dumps({'type':'progress','msg':'Fetching selected pair'}),flush=True)\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.02)\n"
        f"print({_done(complete, 'error')!r},flush=True)\nsys.exit(1)\n",
    )
    row = {
        "raw": {"id": "selected"},
        "selection_id": "source/selected",
        "machine": "local",
        "env": "Win",
    }
    executor = None
    tasks = maintenance.build_tasks(
        "sync",
        [row],
        None,
        project="demo",
        local=("local", "Win"),
        on_progress=lambda key, update: executor.report(key, update),
    )
    executor = maintenance.MaintenanceExecutor("sync", tasks)
    screen = SimpleNamespace(
        executor=executor,
        spin=lambda: "*",
        debug="",
        progress={
            "op": "sync",
            "verb": "Sync",
            "scope": "selected",
            "done": False,
            "armed": True,
            "items": [
                {"key": "source/selected", "id4": "selected", "title": "Task", "state": "pending"}
            ],
        },
    )
    thread = threading.Thread(target=executor._run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            PickerScreenMaintenanceActionsMixin._poll_executor(screen)
            text = ProgressScreen(screen)._panel().renderable.plain
            if "Fetching selected pair" in text:
                break
            time.sleep(0.02)
        assert "Fetching selected pair" in text and not screen.progress["done"]
        PickerScreenInputMixin._key_progress(screen, "escape")
        assert screen.executor is executor and screen.progress is not None
        assert "does not cancel" in screen.debug
    finally:
        release.touch()
        thread.join(10)
    assert not thread.is_alive()
    PickerScreenMaintenanceActionsMixin._poll_executor(screen)
    assert executor.result("source/selected") == complete
    text = ProgressScreen(screen)._panel().renderable.plain
    assert screen.progress["done"] and "knowledge: dirty" in text and "harness: updated" in text
    assert executor.result("other-source/selected") is None


@pytest.mark.parametrize(
    "body,expected",
    [
        ("print('unframed')", "invalid Sync"),
        ("print('[]')", "invalid Sync"),
        ("print(json.dumps({'type':'done','result':[]}))", "invalid Sync"),
        ("print(json.dumps({'type':'progress','msg':'Waiting'}))", "without a complete terminal"),
        (f"print({_done()!r});sys.exit(1)", "contradicts"),
        (f"print({_done({'ok': False}, 'error')!r})", "contradicts"),
        (f"print({_done()!r});print({_done()!r})", "invalid Sync"),
        (f"sys.stdout.write({_done()!r});sys.stdout.flush()", "without a complete terminal"),
    ],
)
def test_process_failures_do_not_succeed_or_replay(monkeypatch, body, expected):
    _provider(monkeypatch, body)
    calls = []
    monkeypatch.setattr(engine_client, "run_json", lambda *_a, **_k: calls.append("replayed"))
    result = engine_sync.run("demo", "selected", execution_timeout=10)
    assert not result["ok"] and result["outcome"] == "unknown"
    assert expected in result["reason"] and "Last stage:" in result["detail"]
    assert not calls


def test_stderr_pressure_is_drained_and_terminal_result_preserved(monkeypatch):
    _provider(
        monkeypatch, f"sys.stderr.write('diagnostic\\n'*10000)\nprint({_done()!r},flush=True)"
    )
    result = engine_sync.run("demo", "selected", execution_timeout=10)
    assert result["ok"] and "diagnostic" in result["detail"]


def test_initial_and_interphase_silence_do_not_cancel_and_unknown_updates_renew_contact(
    monkeypatch,
):
    _provider(
        monkeypatch,
        "time.sleep(.35)\n"
        "for _ in range(8):\n"
        " print(json.dumps({'type':'future-keepalive' if _%2 else 'progress'}),flush=True)\n"
        " time.sleep(.06)\n"
        "time.sleep(.35)\n"
        f"print({_done()!r},flush=True)\n",
    )
    updates = []
    result = engine_sync.run(
        "demo",
        "selected",
        report=updates.append,
        communication_timeout=0.2,
        execution_timeout=10,
    )
    assert result["ok"]
    lost = [index for index, value in enumerate(updates) if value["communication_lost"]]
    assert lost
    recovered = next(
        index
        for index in range(lost[0] + 1, len(updates))
        if not updates[index]["communication_lost"]
    )
    assert any(value["communication_lost"] for value in updates[recovered:])
    assert any(value["elapsed"] > 0.5 and not value["communication_lost"] for value in updates)


def test_execution_clock_expires_despite_progress_and_retires_owned_child(tmp_path, monkeypatch):
    heartbeat = tmp_path / "child"
    child = (
        "import time\nfrom pathlib import Path\nwhile True:\n"
        f" Path({str(heartbeat)!r}).write_text(str(time.monotonic()))\n time.sleep(.02)\n"
    )
    _provider(
        monkeypatch,
        "import subprocess\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}])\n"
        "while True:\n"
        " print(json.dumps({'type':'progress','msg':'Waiting for nested work'}),flush=True)\n"
        " time.sleep(.05)\n",
    )
    updates = []
    result = engine_sync.run(
        "demo",
        "selected",
        report=updates.append,
        communication_timeout=0.2,
        execution_timeout=2,
    )
    assert not result["ok"] and "execution budget expired" in result["reason"]
    assert "Waiting for nested work" in result["detail"]
    assert heartbeat.exists()
    value = heartbeat.read_text()
    time.sleep(0.15)
    assert heartbeat.read_text() == value
    assert not any(t.name == "sync-progress" for t in threading.enumerate())


def test_cleanup_failure_is_not_swallowed_as_a_sync_result(monkeypatch):
    from worktree_manager.engine_posix_process import EngineCleanupError

    _provider(monkeypatch, "pass")
    monkeypatch.setattr(
        engine_sync.engine_process,
        "run",
        lambda *_a, **_kw: (_ for _ in ()).throw(EngineCleanupError("unconfirmed exact child")),
    )
    with pytest.raises(EngineCleanupError, match="unconfirmed exact child") as caught:
        engine_sync.run("demo", "selected")
    retained = caught.value.output_directory
    assert retained.is_dir() and (retained / "stdout").is_file()
    shutil.rmtree(retained)
    error = EngineCleanupError("unconfirmed exact child")
    executor = maintenance.MaintenanceExecutor(
        "sync",
        [
            ("selected", lambda: (_ for _ in ()).throw(error)),
            ("later", lambda: pytest.fail("unsettled run continued mutating")),
        ],
    )
    executor._run()
    assert executor.cleanup_pending() and executor._cleanup_errors["selected"] is error
    assert executor.result("selected")["cleanup_pending"]
    assert executor.state("later") == maintenance.PENDING
    from worktree_manager.production_picker.picker_tui.engine_input import PickerScreenInputMixin

    progress = {"op": "sync", "done": True, "armed": True, "items": []}
    screen = SimpleNamespace(progress=progress, executor=executor, debug="")
    PickerScreenInputMixin._key_progress(screen, "escape")
    assert screen.progress is progress and screen.executor is executor
    assert "custody is retained" in screen.debug


@pytest.mark.parametrize("stream", [False, True])
def test_cleanup_failure_retains_complete_partial_result_and_stderr(monkeypatch, stream):
    from worktree_manager.engine_posix_process import EngineCleanupError

    partial = {
        "ok": False,
        "reason": "pair-sync-blocked",
        "detail": "knowledge: ff-failed",
        "plugins": "skipped",
        "pair_results": [
            {"role": "harness", "updated": True, "reason": "updated"},
            {"role": "knowledge", "updated": False, "reason": "ff-failed"},
        ],
    }
    _provider(monkeypatch, "pass")
    monkeypatch.setattr(engine_sync, "supports_progress", lambda _command: stream)
    error = EngineCleanupError("fixture retirement unconfirmed")
    error.returncode = 1

    def failed_owner(argv, **kwargs):
        if stream:
            kwargs["stdout"].write((_done(partial, "error") + "\n").encode())
            kwargs["stdout"].flush()
            kwargs["stderr"].write(b"bounded child diagnostic")
            kwargs["stderr"].flush()
        else:
            error.stdout = json.dumps(partial)
            error.stderr = "bounded child diagnostic"
        raise error

    monkeypatch.setattr(engine_sync.engine_process, "run", failed_owner)
    executor = maintenance.MaintenanceExecutor(
        "sync",
        [
            ("selected", lambda: engine_sync.run("demo", "selected", cancelled=threading.Event())),
        ],
    )
    executor._run()
    result = executor.result("selected")
    assert result["pair_results"] == partial["pair_results"] and result["plugins"] == "skipped"
    assert "knowledge: ff-failed" in result["detail"]
    assert "bounded child diagnostic" in result["detail"]
    assert not result["ok"] and result["outcome"] == "unknown" and result["cleanup_pending"]
    assert executor._cleanup_errors["selected"] is error
    with pytest.raises(EngineCleanupError) as caught:
        executor.close()
    assert caught.value is error
    if stream:
        shutil.rmtree(error.output_directory)


def test_remote_sync_keeps_its_existing_json_path(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_ssh

    monkeypatch.setattr(engine_sync, "run", lambda *_a, **_kw: pytest.fail("local stream"))
    monkeypatch.setattr(data_ssh, "remote_op_argv", lambda *a, **k: ["qualified-remote"])
    calls = []
    monkeypatch.setattr(maintenance, "_ssh_json", lambda argv: calls.append(argv) or {"ok": True})
    assert maintenance._make_task("sync", "selected", "remote", "Win", False, project="demo")()[
        "ok"
    ]
    assert calls == [["qualified-remote"]]


@pytest.mark.parametrize(
    "targets",
    [
        ("remote",),
        ("remote", "local"),
        ("local", "remote"),
    ],
)
def test_remote_and_mixed_teardown_stop_queued_items_without_adopting_ssh(monkeypatch, targets):
    from worktree_manager.production_picker.picker_tui import data_ssh

    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def remote(_argv):
        calls.append("remote")
        entered.set()
        assert release.wait(5), "fixture remote gate not released"
        return {"ok": True}

    def local(_project, _id, **kwargs):
        calls.append("local")
        entered.set()
        assert kwargs["cancelled"].wait(5), "owned local stop not received"
        return {"ok": False, "reason": "owner closed", "outcome": "unknown"}

    monkeypatch.setattr(data_ssh, "remote_op_argv", lambda *a, **k: ["qualified-remote"])
    monkeypatch.setattr(maintenance, "_ssh_json", remote)
    monkeypatch.setattr(engine_sync, "run", local)
    rows = [
        {"raw": {"id": target}, "selection_id": target, "machine": target, "env": "Win"}
        for target in targets
    ]
    executor = maintenance.MaintenanceExecutor(
        "sync",
        maintenance.build_tasks(
            "sync",
            rows,
            None,
            project="demo",
            local=("local", "Win"),
            cancelled=cancelled,
        ),
        cancelled=cancelled,
    )
    executor.start()
    join = executor._thread.join

    def observe_join(timeout):
        assert targets[0] == "local", "teardown waited on the unchanged remote SSH lifetime"
        join(timeout)

    monkeypatch.setattr(executor._thread, "join", observe_join)
    try:
        assert entered.wait(5)
        executor.close()
        assert calls == [targets[0]]
        if targets[0] == "remote":
            assert executor._thread.is_alive() and not executor.is_done()
        else:
            assert not executor._thread.is_alive() and executor.is_done()
    finally:
        release.set()
        cancelled.set()
        join(5)
    assert not executor._thread.is_alive()
    assert calls == [targets[0]]
    for target in targets[1:]:
        assert executor.state(target) == maintenance.PENDING and executor.result(target) is None


def test_output_limit_is_a_visible_unknown_outcome_without_replay(monkeypatch):
    _provider(monkeypatch, "print('x'*2000,flush=True)\ntime.sleep(10)")
    monkeypatch.setattr(engine_sync, "_MAX_FRAME", 256)
    result = engine_sync.run("demo", "selected", execution_timeout=5)
    assert not result["ok"] and result["outcome"] == "unknown"
    assert "byte budget" in result["reason"]
