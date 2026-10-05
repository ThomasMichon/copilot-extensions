from __future__ import annotations

import os
import subprocess
from pathlib import Path

from zdd import routing

from worktree_manager import daemons_status


class _FakeControlClient:
    """Stand-in for ``mux_daemon_cutover.ControlClient`` keyed by port."""

    responses: dict[int, dict] = {}

    def __init__(self, base_url: str, *, root=None, timeout: float = 5.0):
        # base_url is "http://127.0.0.1:<port>"
        self.port = int(base_url.rsplit(":", 1)[1])

    def health(self) -> dict:
        response = self.responses.get(self.port)
        if response is None:
            raise ConnectionError("unreachable")
        return response


def _active_table(pid: int, port: int) -> dict:
    return {
        "active": {
            "bind": "127.0.0.1",
            "port": port,
            "pid": pid,
            "version": "0.1.0-dev1",
            "generation": 3,
        }
    }


def test_daemon_statuses_marks_a_pre_upgrade_daemons_telemetry_unsupported(
    tmp_path: Path, monkeypatch
):
    """A reachable daemon running code older than this feature answers with
    the old, smaller health shape (just ``{"status": ...}``, no "version"
    key at all) -- exactly the long-resident stale daemons copilot-
    extensions#5001 is about. Representing that as null version/
    attached_clients/busy would misleadingly look like "live, measured, and
    idle" rather than "can't be measured"; it must be marked explicitly
    unsupported instead."""
    root = tmp_path / "root"
    root.mkdir()

    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: {555}
    )
    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: True,
    )
    monkeypatch.setattr(
        daemons_status, "_cmdline_for_pid", lambda pid: "... --listen-port=9555 ..."
    )
    monkeypatch.setattr(daemons_status, "_pid_owned_by_current_user", lambda pid: True)
    monkeypatch.setattr(routing, "read_table", lambda config_dir: None)
    _FakeControlClient.responses = {9555: {"status": "ready"}}
    monkeypatch.setattr(daemons_status.mux_daemon_cutover, "ControlClient", _FakeControlClient)

    statuses = daemons_status.daemon_statuses(root)

    assert statuses == [
        {
            "pid": 555,
            "port": 9555,
            "active": False,
            "status": "ready",
            "telemetry": "unsupported",
        }
    ]


def test_daemon_statuses_reports_active_reachable_and_unreachable(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()

    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_iter_mux_daemon_pids",
        lambda: {101, 202, 303},
    )
    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: pid in (101, 202, 303),
    )
    monkeypatch.setattr(
        daemons_status,
        "_cmdline_for_pid",
        lambda pid: {
            101: "... --listen-port=9101 ...",
            202: "... --listen-port=9202 ...",
            303: "... (no port flag recoverable) ...",
        }[pid],
    )
    monkeypatch.setattr(
        routing, "read_table", lambda config_dir: _active_table(pid=101, port=9101)
    )
    monkeypatch.setattr(daemons_status, "_pid_owned_by_current_user", lambda pid: True)
    _FakeControlClient.responses = {
        9101: {
            "status": "ready",
            "version": "0.1.0-dev1",
            "attached_clients": 2,
            "busy": False,
        },
        9202: {
            "status": "draining",
            "version": "0.1.0-dev0",
            "attached_clients": 1,
            "busy": True,
        },
        # 9303 intentionally absent -- simulates an unreachable daemon.
    }
    monkeypatch.setattr(daemons_status.mux_daemon_cutover, "ControlClient", _FakeControlClient)

    statuses = daemons_status.daemon_statuses(root)

    by_pid = {entry["pid"]: entry for entry in statuses}
    assert by_pid[101] == {
        "pid": 101,
        "port": 9101,
        "active": True,
        "status": "ready",
        "version": "0.1.0-dev1",
        "attached_clients": 2,
        "busy": False,
    }
    assert by_pid[202] == {
        "pid": 202,
        "port": 9202,
        "active": False,
        "status": "draining",
        "version": "0.1.0-dev0",
        "attached_clients": 1,
        "busy": True,
    }
    assert by_pid[303] == {"pid": 303, "port": None, "active": False, "status": "unknown"}


def test_daemon_statuses_marks_a_failed_health_request_unreachable(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()

    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: {404}
    )
    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: True,
    )
    monkeypatch.setattr(
        daemons_status, "_cmdline_for_pid", lambda pid: "... --listen-port=9404 ..."
    )
    monkeypatch.setattr(daemons_status, "_pid_owned_by_current_user", lambda pid: True)
    monkeypatch.setattr(routing, "read_table", lambda config_dir: None)
    _FakeControlClient.responses = {}
    monkeypatch.setattr(daemons_status.mux_daemon_cutover, "ControlClient", _FakeControlClient)

    statuses = daemons_status.daemon_statuses(root)

    assert statuses == [
        {"pid": 404, "port": 9404, "active": False, "status": "unreachable"}
    ]


def test_daemon_statuses_requires_port_match_not_just_pid_for_active(
    tmp_path: Path, monkeypatch
):
    """After PID reuse, the routing table's active row can retain a stale
    endpoint whose pid a later, unrelated daemon happens to reuse on a
    DIFFERENT port -- matching pid alone would misreport that unrelated
    daemon as active."""
    root = tmp_path / "root"
    root.mkdir()

    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: {101}
    )
    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: True,
    )
    monkeypatch.setattr(
        daemons_status, "_cmdline_for_pid", lambda pid: "... --listen-port=9999 ..."
    )
    monkeypatch.setattr(daemons_status, "_pid_owned_by_current_user", lambda pid: True)
    # Routing table's active row still names pid 101, but on the OLD port
    # (9101) -- not the port this pid is actually listening on now (9999).
    monkeypatch.setattr(
        routing, "read_table", lambda config_dir: _active_table(pid=101, port=9101)
    )
    _FakeControlClient.responses = {9999: {"status": "ready"}}
    monkeypatch.setattr(daemons_status.mux_daemon_cutover, "ControlClient", _FakeControlClient)

    statuses = daemons_status.daemon_statuses(root)

    assert statuses[0]["active"] is False


def test_daemon_statuses_never_connects_to_a_candidate_with_unverified_owner(
    tmp_path: Path, monkeypatch
):
    """Another local account could run a lookalike process reporting the
    same ``--root=`` value in its own command line and listening on a port
    -- without an OS-owner check, a blind probe here would hand that
    process our bearer token. A candidate whose owner cannot be confirmed
    to be the current user must be reported without ever constructing a
    ``ControlClient`` (and therefore never sending the token) at all."""
    root = tmp_path / "root"
    root.mkdir()

    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: {666}
    )
    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: True,
    )
    monkeypatch.setattr(
        daemons_status, "_cmdline_for_pid", lambda pid: "... --listen-port=9666 ..."
    )
    monkeypatch.setattr(daemons_status, "_pid_owned_by_current_user", lambda pid: False)
    monkeypatch.setattr(routing, "read_table", lambda config_dir: None)

    connected = []

    class _NeverCall:
        def __init__(self, *a, **k):
            connected.append(True)

    monkeypatch.setattr(daemons_status.mux_daemon_cutover, "ControlClient", _NeverCall)

    statuses = daemons_status.daemon_statuses(root)

    assert not connected
    assert statuses == [
        {"pid": 666, "port": 9666, "active": False, "status": "unverified-owner"}
    ]


def test_daemon_statuses_scopes_to_requested_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()

    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: {1, 2}
    )
    monkeypatch.setattr(
        daemons_status.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: pid == 1,
    )
    monkeypatch.setattr(routing, "read_table", lambda config_dir: None)
    seen_pids: list[int] = []
    monkeypatch.setattr(
        daemons_status,
        "_cmdline_for_pid",
        lambda pid: seen_pids.append(pid) or "",
    )

    statuses = daemons_status.daemon_statuses(root)

    assert seen_pids == [1]
    assert statuses == [{"pid": 1, "port": None, "active": False, "status": "unknown"}]


def test_parse_listen_port_handles_missing_and_present_flag():
    assert daemons_status._parse_listen_port("--listen-port=1234") == 1234
    assert daemons_status._parse_listen_port("nothing here") is None
    assert daemons_status._parse_listen_port("--listen-port=notanumber") is None


def test_pid_owned_by_current_user_windows_matches_and_mismatches(monkeypatch):
    """SID-based, not env-var-based: ``USERDOMAIN``/``USERNAME`` are
    trivially spoofable by whatever spawned this process, so the ownership
    check must come entirely from the OS's own SID primitives, never from
    comparing against those environment values."""
    monkeypatch.setattr(os, "name", "nt")
    current_sid = "S-1-5-21-1111111111-2222222222-3333333333-1001"
    owner_sid = current_sid

    def _fake_run(argv, **kwargs):
        if "WindowsIdentity" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, stdout=current_sid + "\n")
        return subprocess.CompletedProcess(argv, 0, stdout=owner_sid + "\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert daemons_status._pid_owned_by_current_user(123) is True

    owner_sid = "S-1-5-21-9999999999-8888888888-7777777777-1002"
    assert daemons_status._pid_owned_by_current_user(123) is False

    owner_sid = ""
    assert daemons_status._pid_owned_by_current_user(123) is False

    owner_sid = current_sid
    current_sid = ""
    assert daemons_status._pid_owned_by_current_user(123) is False


def test_pid_owned_by_current_user_posix_ps_fallback_matches_and_mismatches(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    # Force the ps fallback deterministically regardless of this test's own
    # host OS: a bare "/proc/<pid>" is never a real directory on a Windows
    # test runner, but an actual Linux CI host DOES have a real /proc -- an
    # unstubbed Path.is_dir() there would let the helper read that host's
    # genuine process table instead of this test's injected ps output.
    monkeypatch.setattr(daemons_status.Path, "is_dir", lambda self: False)

    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="501\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert daemons_status._pid_owned_by_current_user(123) is True

    def _fake_run_other(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="999\n")

    monkeypatch.setattr(subprocess, "run", _fake_run_other)
    assert daemons_status._pid_owned_by_current_user(123) is False

    def _fake_run_garbage(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="not-a-uid\n")

    monkeypatch.setattr(subprocess, "run", _fake_run_garbage)
    assert daemons_status._pid_owned_by_current_user(123) is False


def test_pid_owned_by_current_user_handles_a_timed_out_probe(monkeypatch):
    """A probe that times out or can't start must never crash the whole
    status report (``check=False`` on ``subprocess.run`` does not suppress
    these) -- it must be treated the same as any other unverifiable
    ownership: ``False``, not a propagated exception."""
    monkeypatch.setattr(os, "name", "nt")

    def _raise(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert daemons_status._pid_owned_by_current_user(123) is False

