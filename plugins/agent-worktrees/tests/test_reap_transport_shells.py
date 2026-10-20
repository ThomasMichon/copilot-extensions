"""Remote transport clients keep their launcher ancestry live during sweeps."""

from __future__ import annotations

import pytest

from agent_worktrees import __main__ as cli


NOW = 10_000.0
OLD = NOW - 7200


def _process(pid: int, ppid: int, name: str, cmdline: str) -> dict:
    return {
        "pid": pid,
        "ppid": ppid,
        "name": name,
        "cmdline": cmdline,
        "create_epoch": OLD,
        "session_id": 1,
    }


def _transport_tree(transport: str, nested: bool) -> list[dict]:
    processes = [
        _process(100, 99, "python.exe", "python.exe -m agent_worktrees --project demo"),
        _process(200, 199, "python.exe", "python.exe -m agent_worktrees --project demo"),
    ]
    parent = 100
    if nested:
        processes.extend([
            _process(101, 100, "cmd.exe", "cmd.exe /c worktree-manager"),
            _process(102, 101, "python.exe", "python.exe -m worktree_manager"),
            _process(103, 102, "pwsh.exe", "pwsh.exe -File launch-session.ps1"),
        ])
        parent = 103
    processes.append(_process(104, parent, transport, f"{transport} example.com"))
    return processes


@pytest.mark.parametrize("transport", ["ssh.exe", "ssh", "wsl.exe", "wsl"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "manager-chain"])
def test_live_transport_spares_every_launcher_ancestor(
    transport: str, nested: bool,
) -> None:
    processes = _transport_tree(transport, nested)
    live_pids = {process["pid"] for process in processes}
    reaped, skipped = cli.select_orphan_launcher_shells(
        processes, now=NOW, idle_grace_secs=3600, self_pid=900,
        pid_alive=lambda pid: pid in live_pids,
    )
    assert [process["pid"] for process in reaped] == [200]
    reasons = {entry["pid"]: entry["reason"] for entry in skipped}
    assert reasons[100] == "live-descendant"
    if nested:
        assert reasons[103] == "live-descendant"


@pytest.mark.parametrize("transport", ["ssh.exe", "ssh", "wsl.exe", "wsl"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "manager-chain"])
def test_terminating_sweep_never_targets_live_transport_launchers(
    monkeypatch: pytest.MonkeyPatch, transport: str, nested: bool,
) -> None:
    processes = _transport_tree(transport, nested)
    live_pids = {process["pid"] for process in processes}
    terminated: list[int] = []

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        return True

    monkeypatch.setattr(cli, "_enumerate_launcher_shells", lambda: processes)
    monkeypatch.setattr(cli.locks, "pid_alive", lambda pid: pid in live_pids)
    monkeypatch.setattr(cli.procs, "terminate_pid", terminate)
    monkeypatch.setattr(cli.activity, "log_event", lambda *args, **kwargs: None)
    report = cli.reap_orphan_launcher_shells(
        now=NOW, idle_grace_secs=3600, dry_run=False,
    )
    assert report["reaped"] == [200]
    assert report["candidates"] == [{
        "pid": 200, "cmdline": processes[1]["cmdline"],
    }]
    assert report["errors"] == []
    assert terminated == [200]
