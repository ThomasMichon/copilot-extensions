from __future__ import annotations

from pathlib import Path

from agent_worktrees import daemon_health


def test_candidates_are_scoped_to_the_active_runtime_home(tmp_path: Path, monkeypatch):
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
