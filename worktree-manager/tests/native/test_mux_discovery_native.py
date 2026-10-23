"""Contained Windows process proof; a protocol fixture is not a human mux."""

from __future__ import annotations

import json
import sys

import pytest
from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client

from worktree_manager import mux_daemon, mux_discovery
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


@pytest.mark.skipif(sys.platform != "win32", reason="Windows descendant-Job boundary")
def test_native_probe_and_discovery_ipc_survive_manager_reconstruction(tmp_path, monkeypatch):
    # Python interprets the fixture's command-named script; no shell, live mux,
    # provider registration or host-side installation participates.
    listing = "wt-one\t1\t$1:100\t" + str(tmp_path) + "\n"
    (tmp_path / "list-sessions").write_text("print(" + repr(listing) + ")", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    payload = dict(project="project", worktree_id="one", worktree_path=str(tmp_path),
                   mux_session="wt-one", mux_bin=sys.executable,
                   session_incarnation="$1:100", attached_clients=1)
    first = mux_discovery.discover_mapping(registry, payload)
    assert first["applied"]
    server = CoalescingServer(
        mux_daemon.build_compute(MuxMappingRegistry(registry._path)),
        linger_seconds=1, subscriber_ttl=3,
    )
    server.start()
    rendezvous = server.rendezvous()
    host, port = rendezvous["endpoint"].rsplit(":", 1)
    client_id = client.new_client_id()
    try:
        result = client.request(
            host, int(port), rendezvous["token"], kind=mux_discovery.KIND,
            key=json.dumps(payload, sort_keys=True), payload=payload,
            request_deadline_s=5, client_id=client_id,
        )
        assert result["applied"]
        assert result["entry"]["mapping_revision"] == 1
        assert result["entry"]["session_incarnation"] == "$1:100"
        assert result["entry"]["attached_clients"] == 1
    finally:
        client.release(host, int(port), rendezvous["token"], client_id, timeout=2)
        server.close()
