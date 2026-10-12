from __future__ import annotations

from pathlib import Path
import pytest

from agent_worktrees import daemon_health


def test_candidates_are_scoped_to_the_active_runtime_home(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(daemon_health.sys, "platform", "linux")
    runtime_home = tmp_path / "cell-a"
    runtime_home.mkdir()
    monkeypatch.setattr(
        daemon_health.status_monitor_runtime,
        "_aw_runtime_home",
        lambda: runtime_home,
    )
    monkeypatch.setattr(
        daemon_health.status_monitor_cutover,
        "_iter_status_monitor_pids",
        lambda: {101, 202},
    )
    monkeypatch.setattr(
        daemon_health.procs,
        "processes_with_cwd_under",
        lambda root: (
            [{"pid": 101, "name": "python"}] if root == str(runtime_home) else []
        ),
    )
    monkeypatch.setattr(
        daemon_health.locks,
        "process_start_time",
        lambda pid: f"start-{pid}",
    )

    candidates = daemon_health._candidates()

    assert [candidate.pid for candidate in candidates] == [101]
    assert candidates[0].start_time == "start-101"


def test_candidates_use_darwin_cwd_probe_when_procfs_is_unavailable(
    tmp_path: Path, monkeypatch
):
    runtime_home = tmp_path / "cell-a"
    runtime_home.mkdir()
    monkeypatch.setattr(daemon_health.sys, "platform", "darwin")
    monkeypatch.setattr(
        daemon_health.status_monitor_runtime,
        "_aw_runtime_home",
        lambda: runtime_home,
    )
    monkeypatch.setattr(
        daemon_health.status_monitor_cutover,
        "_iter_status_monitor_pids",
        lambda: {101, 202},
    )
    monkeypatch.setattr(
        daemon_health,
        "_darwin_pid_under_root",
        lambda pid, runtime_root: pid == 202 and runtime_root == str(runtime_home),
    )
    monkeypatch.setattr(
        daemon_health.locks,
        "process_start_time",
        lambda pid: f"start-{pid}",
    )

    candidates = daemon_health._candidates()

    assert [candidate.pid for candidate in candidates] == [202]


def test_windows_candidates_use_one_fresh_native_snapshot(tmp_path, monkeypatch):
    from agent_worktrees import windows_process_census

    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    calls = []

    def snapshot():
        calls.append(True)
        return [
            {"pid": 101, "cwd": str(tmp_path), "start_time": "owner",
             "command_line": '"python.exe" -m agent_worktrees status-monitor --passive'},
            {"pid": 202, "cwd": str(tmp_path.parent / "another-cell"), "start_time": "other",
             "command_line": "python -m agent_worktrees status-monitor"},
            {"pid": 303, "cwd": str(tmp_path), "start_time": "cli",
             "command_line": "python -m agent_worktrees status"},
        ]

    monkeypatch.setattr(windows_process_census, "snapshot", snapshot)
    assert [c.pid for c in daemon_health._candidates()] == [101]
    assert [c.pid for c in daemon_health._candidates()] == [101]
    assert len(calls) == 2


def test_windows_matching_monitor_without_cwd_blocks_census(tmp_path, monkeypatch):
    from agent_worktrees import windows_process_census

    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: [
        {"pid": 101, "cwd": "", "start_time": "monitor",
         "command_line": "python -m agent_worktrees status-monitor"},
    ])
    with pytest.raises(OSError, match="runtime scope for monitor PID 101"):
        daemon_health._candidates()


def test_windows_interpreter_pair_is_not_reaped_as_duplicate(tmp_path, monkeypatch):
    from agent_worktrees import windows_process_census

    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: [
        {"pid": pid, "cwd": str(tmp_path), "start_time": str(pid),
         "command_line": "python -m agent_worktrees status-monitor"}
        for pid in (101, 202)
    ])
    monkeypatch.setattr(daemon_health.procs, "parent_pid", lambda pid: 101 if pid == 202 else 1)
    with pytest.raises(OSError, match="parent/child interpreter pair"):
        daemon_health._candidates()
