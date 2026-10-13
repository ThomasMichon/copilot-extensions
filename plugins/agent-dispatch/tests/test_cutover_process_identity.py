"""A Windows venv trampoline and its server are one coordinator generation."""

import os
from types import SimpleNamespace

import pytest

from agent_dispatch import coordinator_cli


def test_post_cutover_reap_keeps_verified_launcher_and_server(monkeypatch, tmp_path):
    from zdd import routing
    from agent_dispatch import reap
    from agent_dispatch.supervisor_processes import WindowsProcess

    from agent_dispatch import cutover_process as identity

    command = "python -m agent_dispatch serve --port 1234 --passive"
    rows = [
        WindowsProcess(10, 1, command_line=command),
        WindowsProcess(11, 10, command_line=command),
        WindowsProcess(20, 1, command_line=command.replace("1234", "1233")),
    ]
    tokens = {10: "100", 11: "101", 20: "90"}
    monkeypatch.setattr(identity, "is_windows", lambda: True)
    monkeypatch.setattr(identity, "iter_windows_processes", lambda: rows)
    monkeypatch.setattr(identity, "process_start_time", tokens.get)
    monkeypatch.setattr(coordinator_cli._config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(routing, "read_table", lambda _: {
        "active": {"port": 1234, "pid": 11, "process_start_time": "101"},
    })
    killed = []
    real_reap = reap.reap_superseded_coordinators
    monkeypatch.setattr(reap, "reap_superseded_coordinators", lambda **kw: real_reap(
        **kw, list_procs=lambda: [reap.CoordProc(p.pid, p.command_line) for p in rows],
        terminate=lambda pid: killed.append(pid) or True,
    ))
    result = SimpleNamespace(new_port=1234, steps=[])
    coordinator_cli._reap_superseded_coordinators(result)
    assert killed == [20]


@pytest.mark.parametrize("mode", ["unknown", "reused", "multiple", "wrong-command"])
def test_ambiguous_server_identity_refuses_promotion(monkeypatch, mode):
    from agent_dispatch import cutover_process as identity
    from agent_dispatch.supervisor_processes import WindowsProcess

    command = "python -m agent_dispatch serve --port 1234 --passive"
    rows = [WindowsProcess(10, 1, command_line=command),
            WindowsProcess(11, 10, command_line=command)]
    tokens = {10: "100", 11: "101"}
    monkeypatch.setattr(identity, "process_start_time", tokens.get)
    handle = identity.CoordinatorProcess(SimpleNamespace(pid=10))
    if mode == "unknown":
        tokens[11] = None
    elif mode == "reused":
        tokens[10] = "200"
    elif mode == "multiple":
        rows.append(WindowsProcess(12, 10, command_line=command))
        tokens[12] = "102"
    else:
        rows[1] = WindowsProcess(11, 10, command_line=command + " --other")
    monkeypatch.setattr(identity, "iter_windows_processes", lambda: rows)
    assert not handle.accept_health({"slot": {"pid": 11}})
    assert handle.pid == 10


def test_health_adopts_server_not_launcher(monkeypatch):
    from agent_dispatch import cutover_process as identity
    from agent_dispatch.supervisor_processes import WindowsProcess

    command = "python -m agent_dispatch serve --port 1234 --passive"
    monkeypatch.setattr(identity, "process_start_time", {10: "100", 11: "101"}.get)
    monkeypatch.setattr(identity, "iter_windows_processes", lambda: [
        WindowsProcess(10, 1, command_line=command),
        WindowsProcess(11, 10, command_line=command),
    ])
    handle = identity.CoordinatorProcess(SimpleNamespace(pid=10))
    assert handle.accept_health({"slot": {"pid": 11}})
    assert handle.pid == 11


@pytest.mark.parametrize("token", [None, "reused"])
def test_post_cutover_identity_failure_skips_reap(monkeypatch, tmp_path, token):
    from agent_dispatch import cutover_process as identity, reap
    from zdd import routing

    monkeypatch.setattr(identity, "is_windows", lambda: True)
    monkeypatch.setattr(identity, "process_start_time", lambda _pid: token)
    monkeypatch.setattr(coordinator_cli._config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(routing, "read_table", lambda _: {
        "active": {"port": 1234, "pid": 11, "process_start_time": "101"},
    })
    monkeypatch.setattr(reap, "reap_superseded_coordinators", lambda **_: pytest.fail("unsafe reap"))
    result = SimpleNamespace(new_port=1234, steps=[])
    coordinator_cli._reap_superseded_coordinators(result)
    assert "reap skipped: coordinator identity" in result.steps[-1]


def test_reused_parent_does_not_claim_server_family(monkeypatch):
    from agent_dispatch import cutover_process as identity
    from agent_dispatch.supervisor_processes import WindowsProcess

    command = "python -m agent_dispatch serve --port 1234 --passive"
    monkeypatch.setattr(identity, "process_start_time", {10: "200", 11: "101"}.get)
    monkeypatch.setattr(identity, "iter_windows_processes", lambda: [
        WindowsProcess(10, 1, command_line=command),
        WindowsProcess(11, 10, command_line=command),
    ])
    with pytest.raises(RuntimeError, match="reused PID"):
        identity.verified_pair(11, "101")


def test_abandoned_breadcrumb_never_reaps_promoted_launcher(monkeypatch, tmp_path):
    from agent_dispatch import cutover_process as identity, reap
    from zdd import breadcrumb, diagnostics, routing

    monkeypatch.setattr(identity, "is_windows", lambda: True)
    monkeypatch.setattr(identity, "verified_pair", lambda *_: {10: "100", 11: "101"})
    reads = []

    def read_table(_config_dir, *, strict=False):
        reads.append(strict)
        return {"active": routing.Endpoint(
            bind="127.0.0.1", port=1234, pid=11, process_start_time="101",
        ).to_dict()}

    monkeypatch.setattr(routing, "read_table", read_table)
    record = {"new_pid": 10, "started_at": "2000-01-01T00:00:00Z", "state": "flipped"}
    monkeypatch.setattr(
        diagnostics, "terminate_pid_if_identity",
        lambda *_args, **_kwargs: pytest.fail("promoted launcher killed"),
    )
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda _: record)
    result = reap.reap_abandoned_passive_backstop(tmp_path, record=record, grace_seconds=0)
    assert not result["reaped"]
    assert "promoted" in result["reason"]
    assert reads == [False, True]


@pytest.mark.parametrize("adopted", [True, False], ids=["healthy-server", "pre-health-child"])
def test_rollback_terminates_verified_child_not_only_launcher(monkeypatch, adopted):
    from agent_dispatch import cutover_process as identity

    killed = []
    launcher = SimpleNamespace(
        pid=10, poll=lambda: None, terminate=lambda: killed.append(10),
    )
    monkeypatch.setattr(identity, "process_start_time", {10: "100", 11: "101"}.get)
    handle = identity.CoordinatorProcess(launcher)
    monkeypatch.setattr(identity, "verified_pair", lambda *_: {10: "100", 11: "101"})
    monkeypatch.setattr(identity, "terminate_pid_if_identity", lambda pid, token: (
        killed.append((pid, token)) or {"killed": True}
    ))
    if adopted:
        assert handle.accept_health({"slot": {"pid": 11}})
    handle.terminate()
    assert killed == [(11, "101"), 10]


def test_launcher_exit_is_not_server_exit(monkeypatch):
    from agent_dispatch import cutover_process as identity

    tokens = {10: "100", 11: "101"}
    launcher = SimpleNamespace(pid=10, poll=lambda: 0)
    monkeypatch.setattr(identity, "process_start_time", tokens.get)
    handle = identity.CoordinatorProcess(launcher)
    monkeypatch.setattr(identity, "verified_pair", lambda *_: dict(tokens))
    assert handle.accept_health({"slot": {"pid": 11}})
    tokens[10] = None
    assert handle.accept_health({"slot": {"pid": 11}})
    monkeypatch.setattr(identity, "_identity_running", lambda *_: True)
    assert handle.poll() is None
    terminated = []
    monkeypatch.setattr(identity, "terminate_pid_if_identity", lambda pid, token: (
        terminated.append((pid, token)) or {"killed": True}
    ))
    handle.terminate()
    assert terminated == [(11, "101")]


@pytest.mark.skipif(os.name != "nt", reason="native Windows process object")
def test_retained_handle_does_not_make_exited_server_look_alive():
    import subprocess
    import sys

    from agent_procutil import no_window_kwargs
    from agent_dispatch import cutover_process as identity

    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    try:
        token = identity.process_start_time(proc.pid)
        assert token
        assert proc.wait(timeout=10) == 0
        # Popen deliberately retains the process handle and its creation time.
        assert identity.process_start_time(proc.pid) == token
        assert identity._identity_running(proc.pid, token) is False
        handle = identity.CoordinatorProcess(SimpleNamespace(pid=1, poll=lambda: 0))
        handle.pid, handle.server_token = proc.pid, token
        assert handle.poll() == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
