"""Tests for the coordinator's rendezvous-file advertising and dynamic bind.

Phase 3 Stage A advertised the bound endpoint; Stage C flips the bind to an
OS-assigned ephemeral port (``127.0.0.1:0``) unless ``AGENT_DISPATCH_PORT`` pins
one, and advertises the *actual* port read back off the socket.
"""

from __future__ import annotations

import socket

import uvicorn

from agent_dispatch import rendezvous, server
from agent_dispatch.config import Config


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_advertise_endpoint_writes_rendezvous(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    path = server.advertise_endpoint(Config(host="127.0.0.1", port=9847))
    assert path is not None
    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.tcp_host_port == ("127.0.0.1", 9847)


def test_advertise_endpoint_reflects_bound_host(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    # NAT bind host (vEthernet IP) + a dynamic port are advertised verbatim.
    server.advertise_endpoint(Config(host="172.19.240.1", port=52731))
    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.tcp_host_port == ("172.19.240.1", 52731)


def test_clear_endpoint_removes_owned_record(tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41001", pid=1001)

    rendezvous.clear_endpoint(run, owner_pid=1001)

    assert rendezvous.read_endpoint(run) is None


def test_clear_endpoint_preserves_successor_owned_record(tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41002", pid=2002)

    rendezvous.clear_endpoint(run, owner_pid=1001)

    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.pid == 2002
    assert ep.tcp_host_port == ("127.0.0.1", 41002)


def test_clear_endpoint_cutover_retains_newest_successor_record(tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41001", pid=1001)
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41002", pid=2002)

    rendezvous.clear_endpoint(run, owner_pid=1001)

    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.pid == 2002
    assert ep.tcp_host_port == ("127.0.0.1", 41002)


def test_clear_endpoint_removes_non_utf8_record(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    rendezvous.endpoint_file(run).write_bytes(b"\xff")

    rendezvous.clear_endpoint(run, owner_pid=1001)

    assert not rendezvous.endpoint_file(run).exists()


def test_clear_endpoint_rechecks_owner_before_unlink(monkeypatch, tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41001", pid=1001)
    owner = rendezvous.read_endpoint(run)
    successor = rendezvous.Endpoint("tcp", "127.0.0.1:41002", pid=2002)
    records = iter((owner, successor))
    monkeypatch.setattr(rendezvous, "read_endpoint", lambda _runtime_dir: next(records))

    rendezvous.clear_endpoint(run, owner_pid=1001)

    assert rendezvous.endpoint_file(run).exists()


def test_server_bind_port_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    assert server._server_bind_port() == 0


def test_server_bind_port_honors_pin(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_PORT", "9847")
    assert server._server_bind_port() == 9847


def test_server_bind_port_ignores_garbage(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_PORT", "not-a-port")
    assert server._server_bind_port() == 0


def test_bind_listen_socket_assigns_os_port():
    sock = server._bind_listen_socket("127.0.0.1", 0)
    try:
        host, port = sock.getsockname()[:2]
        assert host == "127.0.0.1"
        assert port != 0  # OS assigned a real ephemeral port
    finally:
        sock.close()


def _run_serve_capturing(monkeypatch, run, cfg):
    """Run ``serve`` with uvicorn stubbed out, returning the endpoint advertised
    while the server was 'running' plus the sockets uvicorn was handed."""
    # Isolate the routing-table lookup too, not just run_dir() -- otherwise
    # the non-passive liveness guard consults *this real host's* actual
    # routing table and can spuriously refuse to start under `serve(cfg)`
    # whenever a real coordinator happens to be live on the machine running
    # the tests.
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(run.parent / "routing"))
    seen: dict = {}

    def _fake_run(self, sockets=None):
        seen["during"] = rendezvous.read_endpoint(run)
        seen["sockets"] = sockets
        seen["bound_port"] = sockets[0].getsockname()[1] if sockets else None

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)
    server.serve(cfg)
    return seen


def test_serve_binds_os_assigned_port_and_advertises_it(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    cfg = Config(host="127.0.0.1", port=9847, db_path=str(tmp_path / "tasks.db"))

    seen = _run_serve_capturing(monkeypatch, run, cfg)

    # A real OS-assigned port was bound -- not the legacy fixed 9847 -- and the
    # rendezvous file advertises exactly that bound port while serving.
    assert seen["during"] is not None
    advertised_host, advertised_port = seen["during"].tcp_host_port
    assert advertised_host == "127.0.0.1"
    assert advertised_port == seen["bound_port"]
    assert advertised_port != 9847
    # Cleared on shutdown.
    assert rendezvous.read_endpoint(run) is None


def test_serve_honors_pinned_port(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    pinned = _free_port()
    monkeypatch.setenv("AGENT_DISPATCH_PORT", str(pinned))
    cfg = Config(host="127.0.0.1", port=pinned, db_path=str(tmp_path / "tasks.db"))

    seen = _run_serve_capturing(monkeypatch, run, cfg)

    assert seen["bound_port"] == pinned
    assert seen["during"].tcp_host_port == ("127.0.0.1", pinned)
    assert rendezvous.read_endpoint(run) is None


# -- non-passive serve() refuses to seize an already-live route (#3066) -----


def test_serve_raises_when_coordinator_already_live(monkeypatch, tmp_path):
    import pytest

    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: True)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    with pytest.raises(server.CoordinatorAlreadyLiveError):
        server.serve(cfg)

    # Refused before ever binding/advertising/publishing anything.
    assert rendezvous.read_endpoint(run) is None


def test_serve_raises_when_start_lock_already_held(monkeypatch, tmp_path):
    """A second, concurrent non-passive serve() must not slip past the
    friendly caller-side pre-check and seize the route -- the lock itself is
    the atomic guard (ThomasMichon/copilot-extensions#3066 follow-up)."""
    import pytest

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: False)
    # Simulate a concurrent starter already holding the lock. The lock is
    # keyed by routing_dir() (the shared, raced-over resource), not run_dir().
    routing.mkdir(parents=True, exist_ok=True)
    holder = SingleInstance(routing / "serve-start.lock")
    assert holder.acquire()
    try:
        cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
        with pytest.raises(server.CoordinatorAlreadyLiveError):
            server.serve(cfg)
        assert rendezvous.read_endpoint(run) is None
    finally:
        holder.release()


def test_serve_force_bypasses_live_coordinator_guard(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: True)

    def _fake_run(self, sockets=None):
        pass

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    # Must not raise despite a live coordinator, and must actually publish.
    server.serve(cfg, force=True)
    assert rendezvous.read_endpoint(run) is None  # cleared on shutdown, as usual


def test_serve_passive_never_checks_live_coordinator(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))

    def _boom():
        raise AssertionError("passive serve() must never consult liveness")

    monkeypatch.setattr(server, "has_live_local_coordinator", _boom)

    def _fake_run(self, sockets=None):
        pass

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    server.serve(cfg, passive=True)
