from __future__ import annotations

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
    # Routing table's active row still names pid 101, but on the OLD port
    # (9101) -- not the port this pid is actually listening on now (9999).
    monkeypatch.setattr(
        routing, "read_table", lambda config_dir: _active_table(pid=101, port=9101)
    )
    _FakeControlClient.responses = {9999: {"status": "ready"}}
    monkeypatch.setattr(daemons_status.mux_daemon_cutover, "ControlClient", _FakeControlClient)

    statuses = daemons_status.daemon_statuses(root)

    assert statuses[0]["active"] is False


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
