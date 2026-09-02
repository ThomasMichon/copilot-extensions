"""Tests for the SSH port-forward failover transport (ssh_tunnel)."""

from __future__ import annotations

import pytest

from agent_dispatch import ssh_tunnel


# -- endpoint parsing --------------------------------------------------------


def test_parse_endpoint_url():
    assert ssh_tunnel._parse_endpoint("http://127.0.0.1:38798") == ("127.0.0.1", 38798)


def test_parse_endpoint_hostport_with_newline():
    assert ssh_tunnel._parse_endpoint("127.0.0.1:35425\n") == ("127.0.0.1", 35425)


def test_parse_endpoint_takes_last_line():
    assert ssh_tunnel._parse_endpoint("noise\nhttp://10.0.0.2:9000") == ("10.0.0.2", 9000)


def test_parse_endpoint_empty_raises():
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel._parse_endpoint("   ")


def test_parse_endpoint_unparseable_raises():
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel._parse_endpoint("not-a-host-port")


# -- resolve_peer_endpoint: print-endpoint primary + routing-table fallback --


def _fake_capture(mapping):
    """Return an ``_ssh_capture`` stub answering by remote-command substring."""

    def _cap(_exe, _alias, remote_cmd, _timeout):
        for needle, result in mapping.items():
            if needle in remote_cmd:
                if isinstance(result, Exception):
                    raise result
                return result
        raise ssh_tunnel.TunnelUnavailable(f"unexpected remote cmd: {remote_cmd}")

    return _cap


def test_resolve_peer_prefers_print_endpoint(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(
        ssh_tunnel, "_ssh_capture", _fake_capture({"print-endpoint": "http://127.0.0.1:44100"})
    )
    assert ssh_tunnel.resolve_peer_endpoint("peer-host") == ("127.0.0.1", 44100)


def test_resolve_peer_falls_back_to_routing_table(monkeypatch):
    # Older peer: print-endpoint fails, routing table answers.
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(
        ssh_tunnel,
        "_ssh_capture",
        _fake_capture(
            {
                "print-endpoint": ssh_tunnel.TunnelUnavailable("invalid choice"),
                "active.json": '{"active": {"bind": "127.0.0.1", "port": 35425}}',
            }
        ),
    )
    assert ssh_tunnel.resolve_peer_endpoint("peer-host") == ("127.0.0.1", 35425)


def test_resolve_peer_both_fail_raises(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(
        ssh_tunnel,
        "_ssh_capture",
        _fake_capture(
            {
                "print-endpoint": ssh_tunnel.TunnelUnavailable("no cmd"),
                "active.json": "not json",
            }
        ),
    )
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel.resolve_peer_endpoint("peer-host")


def test_resolve_peer_no_ssh_binary(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: None)
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel.resolve_peer_endpoint("peer-host")


def test_ssh_capture_uses_shared_command_helper(monkeypatch):
    captured = {}

    def fake_capture(cmd, *, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(ssh_tunnel, "run_ssh_command", fake_capture)

    assert ssh_tunnel._ssh_capture("ssh", "peer", "true", 3) == "ok"
    assert captured == {
        "cmd": ["ssh", "-o", "BatchMode=yes", "peer", "true"],
        "timeout": 3,
    }


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (OSError("missing config"), "missing config"),
        (ssh_tunnel.subprocess.TimeoutExpired(["ssh"], 3), "timed out after 3 seconds"),
    ],
)
def test_ssh_capture_preserves_launch_failure_detail(monkeypatch, error, detail):
    def fake_command(_cmd, *, timeout):
        raise error

    monkeypatch.setattr(ssh_tunnel, "run_ssh_command", fake_command)

    with pytest.raises(ssh_tunnel.TunnelUnavailable, match=detail):
        ssh_tunnel._ssh_capture("ssh", "peer", "true", 3)


# -- open_coordinator_tunnel: readiness + early-exit handling ----------------


class _FakeProc:
    def __init__(self, *, alive=True):
        self._alive = alive
        self.stderr = None
        self.returncode = 0
        self.terminated = False

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


def test_open_tunnel_returns_when_port_ready(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(ssh_tunnel, "resolve_peer_endpoint", lambda m, **k: ("127.0.0.1", 9000))
    monkeypatch.setattr(ssh_tunnel, "_pick_local_port", lambda: 51999)
    proc = _FakeProc(alive=True)
    captured = {}

    def fake_popen(*args, **kwargs):
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(ssh_tunnel.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        ssh_tunnel, "ssh_subprocess_kwargs", lambda: {"creationflags": 123}
    )
    monkeypatch.setattr(ssh_tunnel, "_port_accepts", lambda port, **k: True)
    monkeypatch.setattr(ssh_tunnel, "_coordinator_reachable", lambda url, **k: True)
    tun = ssh_tunnel.open_coordinator_tunnel("peer-host")
    assert tun.base_url == "http://127.0.0.1:51999"
    tun.close()
    assert proc.terminated
    assert captured["kwargs"]["creationflags"] == 123


def test_open_tunnel_retries_on_stale_port(monkeypatch):
    # First forward reaches no live coordinator (stale port after a cutover);
    # the second, freshly-resolved forward succeeds.
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(ssh_tunnel, "resolve_peer_endpoint", lambda m, **k: ("127.0.0.1", 9000))
    procs = [_FakeProc(alive=True), _FakeProc(alive=True)]
    made = []
    monkeypatch.setattr(ssh_tunnel, "_pick_local_port", lambda: 51000 + len(made))
    monkeypatch.setattr(
        ssh_tunnel.subprocess, "Popen", lambda *a, **k: made.append(1) or procs[len(made) - 1]
    )
    monkeypatch.setattr(ssh_tunnel, "_port_accepts", lambda port, **k: True)
    reachable = iter([False, True])
    monkeypatch.setattr(ssh_tunnel, "_coordinator_reachable", lambda url, **k: next(reachable))
    tun = ssh_tunnel.open_coordinator_tunnel("peer-host")
    assert tun._proc is procs[1]
    assert procs[0].terminated  # the stale first forward was torn down
    tun.close()


def test_open_tunnel_raises_when_never_reachable(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(ssh_tunnel, "resolve_peer_endpoint", lambda m, **k: ("127.0.0.1", 9000))
    monkeypatch.setattr(ssh_tunnel, "_pick_local_port", lambda: 51001)
    monkeypatch.setattr(ssh_tunnel.subprocess, "Popen", lambda *a, **k: _FakeProc(alive=True))
    monkeypatch.setattr(ssh_tunnel, "_port_accepts", lambda port, **k: True)
    monkeypatch.setattr(ssh_tunnel, "_coordinator_reachable", lambda url, **k: False)
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel.open_coordinator_tunnel("peer-host")


def test_open_tunnel_ssh_exits_early_raises(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(ssh_tunnel, "resolve_peer_endpoint", lambda m, **k: ("127.0.0.1", 9000))
    monkeypatch.setattr(ssh_tunnel, "_pick_local_port", lambda: 51998)

    class _DeadProc(_FakeProc):
        def __init__(self):
            super().__init__(alive=False)
            import io

            self.stderr = io.StringIO("permission denied")
            self.returncode = 255

    monkeypatch.setattr(ssh_tunnel.subprocess, "Popen", lambda *a, **k: _DeadProc())
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel.open_coordinator_tunnel("peer-host")


def test_open_tunnel_timeout_terminates_and_raises(monkeypatch):
    monkeypatch.setattr(ssh_tunnel.shutil, "which", lambda _x: "/usr/bin/ssh")
    monkeypatch.setattr(ssh_tunnel, "resolve_peer_endpoint", lambda m, **k: ("127.0.0.1", 9000))
    monkeypatch.setattr(ssh_tunnel, "_pick_local_port", lambda: 51997)
    proc = _FakeProc(alive=True)
    monkeypatch.setattr(ssh_tunnel.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ssh_tunnel, "_port_accepts", lambda port, **k: False)
    with pytest.raises(ssh_tunnel.TunnelUnavailable):
        ssh_tunnel.open_coordinator_tunnel("peer-host", ready_timeout=0.2)
    assert proc.terminated
