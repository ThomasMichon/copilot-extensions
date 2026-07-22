"""Tests for the coordinator's rendezvous-file advertising (Phase 3 Stage A)."""

from __future__ import annotations

from agent_dispatch import rendezvous, server
from agent_dispatch.config import Config


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


def test_serve_advertises_then_clears(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    cfg = Config(host="127.0.0.1", port=9847, db_path=str(tmp_path / "tasks.db"))
    seen: dict = {}

    def _fake_run(app, **kwargs):
        # The rendezvous file must exist while the server is "running".
        seen["during"] = rendezvous.read_endpoint(run)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    server.serve(cfg)
    assert seen["during"] is not None
    assert seen["during"].tcp_host_port == ("127.0.0.1", 9847)
    # Cleared on shutdown.
    assert rendezvous.read_endpoint(run) is None
