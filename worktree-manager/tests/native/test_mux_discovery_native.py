"""Contained Windows process proof; a protocol fixture is not a human mux."""

from __future__ import annotations

import json
import sys

import pytest
from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client

from worktree_manager import mux_daemon, mux_discovery, pane_lifecycle
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


@pytest.mark.skipif(sys.platform != "win32", reason="Windows descendant-Job boundary")
@pytest.mark.parametrize("native_incarnation", ["$1:100", "$3:300"])
def test_pane_start_uses_real_native_probe_before_replacing_a_foreign_tombstone(
    tmp_path, monkeypatch, native_incarnation,
):
    listing = f"%1\t0\twt-one\t{native_incarnation}\t{tmp_path}\n"
    (tmp_path / "list-panes").write_text("print(" + repr(listing) + ")", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "manager"
    registry = MuxMappingRegistry(root / "mux-mapping.json")
    registry.register_next(dict(
        project="project", worktree_id="one", worktree_path=str(tmp_path),
        mux_session="wt-one", mux_bin=sys.executable, session_incarnation="$2:200",
        panes=[{"pane_id": "%2", "role": "cli", "live": True}],
    ))
    registry.remove("project", "one", session_incarnation="$2:200", pane_id="%2", exit_code=7)
    before = registry._path.read_bytes()
    result = pane_lifecycle.admit(
        "start", root=root, expected_installation=None,
        project="project", worktree_id="one", worktree_path=str(tmp_path),
        mux_session="wt-one", mux_bin=sys.executable, session_incarnation="$1:100", pane_id="%1",
    )
    admitted = native_incarnation == "$1:100"
    assert result["result"]["applied"] is admitted
    if admitted:
        assert registry.get("project", "one")["session_incarnation"] == "$1:100"
    else:
        assert registry._path.read_bytes() == before


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
