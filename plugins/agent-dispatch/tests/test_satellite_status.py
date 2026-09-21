"""Tests for the satellite exposure gate + embodiment-status snapshot
(``satellite-agent-exposure`` effort, Phase 1 item C/E):

- :func:`agent_dispatch.config.satellite_gate_open` -- default-closed outbound
  exposure gate.
- :func:`agent_dispatch.tracking.satellite_status_snapshot` -- the
  (worktrees, status) pair pushed by a ``role=satellite`` federation node.
- :class:`agent_dispatch.federation_runner.FederationRunner` wiring both
  together: gated registration + status passthrough for the satellite role
  only.
"""

from __future__ import annotations

import pytest

from agent_dispatch import config, tracking
from agent_dispatch.federation_runner import FederationRunner
from agent_dispatch.satellites import FleetDirectory

# -- config: the gate itself --------------------------------------------------


def test_satellite_gate_closed_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SATELLITE_GATE", raising=False)
    assert config.satellite_gate_open() is False


@pytest.mark.parametrize("value", ["open", "OPEN", "1", "true", "True", "yes", "on"])
def test_satellite_gate_open_values(monkeypatch, value):
    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", value)
    assert config.satellite_gate_open() is True


@pytest.mark.parametrize("value", ["closed", "0", "false", "no", "off", "bogus", ""])
def test_satellite_gate_closed_values(monkeypatch, value):
    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", value)
    assert config.satellite_gate_open() is False


# -- tracking: the status snapshot --------------------------------------------


def test_satellite_status_snapshot_empty_when_no_sessions(monkeypatch):
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: [])
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == []
    assert status == {}


def test_satellite_status_snapshot_maps_sessions(monkeypatch):
    sessions = [
        {
            "worktree_id": "wt-a",
            "session_id": "sess-1",
            "status": "running",
            "turn_state": "running",
            "liveness": "active",
            "updated_at": 123.0,
        },
        {
            "worktree_id": "wt-b",
            "session_id": "sess-2",
            "status": "idle",
            "turn_state": "idle",
            "liveness": "idle",
            "updated_at": 124.0,
        },
    ]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert set(worktrees) == {"wt-a", "wt-b"}
    assert status["wt-a"]["activity"] == "ACTIVE"
    assert status["wt-a"]["session_id"] == "sess-1"
    assert status["wt-b"]["activity"] == "IDLE"


def test_satellite_status_snapshot_skips_sessions_without_worktree_id(monkeypatch):
    sessions = [{"session_id": "sess-1", "status": "running"}]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == []
    assert status == {}


def test_satellite_status_snapshot_includes_minimal_overlay(monkeypatch):
    # worktree_id is itself part of the overlay keys, so a session carrying
    # only that field still produces a (minimal) status entry.
    sessions = [{"worktree_id": "wt-a"}]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == ["wt-a"]
    assert status["wt-a"] == {"worktree_id": "wt-a"}


# -- federation_runner: gate + status wiring ----------------------------------


@pytest.fixture
def directory():
    return FleetDirectory(ttl_seconds=90.0)


def test_satellite_never_registers_while_gate_closed(monkeypatch, directory):
    monkeypatch.delenv("AGENT_DISPATCH_SATELLITE_GATE", raising=False)
    runner = FederationRunner(directory, "book2", role="satellite")
    state = runner.tick()
    assert state["gate_state"] == "closed"
    assert directory.discover_peers() == []


def test_satellite_registers_and_pushes_status_when_gate_open(monkeypatch, directory):
    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", "open")
    monkeypatch.setattr(
        tracking,
        "satellite_status_snapshot",
        lambda: (["wt-a"], {"wt-a": {"activity": "ACTIVE"}}),
    )
    runner = FederationRunner(directory, "book2", role="satellite")
    runner.tick()
    peers = directory.discover_peers()
    assert len(peers) == 1
    assert peers[0]["instance"] == "book2"
    assert peers[0]["worktrees"] == ["wt-a"]
    assert peers[0]["status"] == {"wt-a": {"activity": "ACTIVE"}}


def test_satellite_withdraws_promptly_when_gate_closes_mid_session(monkeypatch, directory):
    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", "open")
    monkeypatch.setattr(tracking, "satellite_status_snapshot", lambda: ([], {}))
    runner = FederationRunner(directory, "book2", role="satellite")
    runner.tick()
    assert len(directory.discover_peers()) == 1

    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", "closed")
    state = runner.tick()
    assert state["gate_state"] == "closed"
    assert directory.discover_peers() == []


def test_peer_role_unaffected_by_satellite_gate_and_pushes_no_status(monkeypatch, directory):
    # The gate + status snapshot are satellite-only; a plain peer must never
    # be gated and never gets worktrees/status pushed on its behalf.
    monkeypatch.delenv("AGENT_DISPATCH_SATELLITE_GATE", raising=False)
    calls = []
    monkeypatch.setattr(
        tracking,
        "satellite_status_snapshot",
        lambda: calls.append("called") or ([], {}),
    )
    runner = FederationRunner(directory, "peer-1", role="peer")
    runner.tick()
    assert calls == []
    peers = directory.discover_peers()
    assert len(peers) == 1
    assert peers[0]["worktrees"] == []
