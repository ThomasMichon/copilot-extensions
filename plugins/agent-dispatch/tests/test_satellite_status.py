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


def test_satellite_status_snapshot_never_boots_the_agent_bridge_daemon(monkeypatch):
    # A periodic federation tick is a passive background probe, not an
    # interactive status request -- it must call list_local_body_sessions
    # with ensure_daemon=False so a down daemon degrades to no rows instead
    # of being booted as a side effect of merely checking.
    calls = []

    def fake_list(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(tracking, "list_local_body_sessions", fake_list)
    tracking.satellite_status_snapshot()
    assert len(calls) == 1
    assert calls[0].get("ensure_daemon") is False


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


@pytest.mark.parametrize("terminal_status", ["stopped", "ended", "failed"])
def test_satellite_status_snapshot_excludes_terminal_sessions(monkeypatch, terminal_status):
    # A resumable-but-stopped (or ended/failed) session occupies the worktree
    # but is not doing live work right now -- must not be advertised as active.
    sessions = [{"worktree_id": "wt-a", "status": terminal_status}]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == []
    assert status == {}


def test_satellite_status_snapshot_never_reports_disconnected_session_as_active(monkeypatch):
    # A session whose transport is disconnected (dead) but still nominally
    # "running" must not be published with activity=ACTIVE -- the worktree is
    # still advertised (it's not a terminal status), just without a live
    # activity label.
    sessions = [{"worktree_id": "wt-a", "status": "running", "liveness": "disconnected"}]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == ["wt-a"]
    assert "activity" not in status["wt-a"]


def test_satellite_status_snapshot_deduplicates_by_worktree_keeping_first_row(monkeypatch):
    # The bridge's session list is newest-first; a worktree can appear more
    # than once after a session roll (retired predecessor + successor). Only
    # the first (newest) row must be kept -- an older row must never
    # overwrite it.
    sessions = [
        {
            "worktree_id": "wt-a",
            "session_id": "sess-new",
            "status": "running",
            "liveness": "active",
        },
        {
            "worktree_id": "wt-a",
            "session_id": "sess-old",
            "status": "running",
            "liveness": "idle",
        },
    ]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == ["wt-a"]
    assert status["wt-a"]["session_id"] == "sess-new"


def test_satellite_status_snapshot_newest_stopped_row_suppresses_older_live_row(monkeypatch):
    # The newest row is authoritative: if it is stopped, an older ("live"-
    # looking) row for the same worktree further down the newest-first list
    # must NOT be resurrected and advertised as active.
    sessions = [
        {"worktree_id": "wt-a", "session_id": "sess-new", "status": "stopped"},
        {
            "worktree_id": "wt-a",
            "session_id": "sess-old",
            "status": "running",
            "liveness": "active",
        },
    ]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda **_: sessions)
    worktrees, status = tracking.satellite_status_snapshot()
    assert worktrees == []
    assert status == {}


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


def test_satellite_retries_deregister_after_a_transient_failure(monkeypatch, directory):
    """Regression test: if deregister() itself raises when the gate closes
    (a transient directory error), the runner must NOT give up and mark
    itself deregistered anyway -- it must retry on the next tick, since the
    whole point of the gate-close path is prompt withdrawal, not a single
    best-effort attempt."""
    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", "open")
    monkeypatch.setattr(tracking, "satellite_status_snapshot", lambda: ([], {}))
    runner = FederationRunner(directory, "book2", role="satellite")
    runner.tick()
    assert len(directory.discover_peers()) == 1

    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", "closed")
    real_deregister = directory.deregister
    calls = []

    def flaky_deregister(instance):
        calls.append(instance)
        if len(calls) == 1:
            raise RuntimeError("transient directory failure")
        return real_deregister(instance)

    monkeypatch.setattr(directory, "deregister", flaky_deregister)

    # First closed-gate tick: deregister fails -> must propagate (so the
    # runner's own run() loop retries) and NOT silently mark _registered
    # False. The entry must still be live in the directory.
    with pytest.raises(RuntimeError):
        runner.tick()
    assert len(directory.discover_peers()) == 1
    assert runner._registered is True

    # Second closed-gate tick: deregister succeeds this time.
    runner.tick()
    assert directory.discover_peers() == []
    assert runner._registered is False


def test_satellite_cleans_up_a_stale_entry_from_a_prior_process(monkeypatch, directory):
    """Regression test: a fresh FederationRunner instance (simulating a
    process restart -- _registered starts False by construction) must still
    attempt deregister on a closed-gate tick even though ITS local flag
    never saw a registration. A previous process for the same stable
    instance id may have registered and then died/restarted with the gate
    now closed; the directory entry must not be gated on this instance's
    own in-memory belief."""
    # Simulate the "previous process" leaving a live registration behind.
    directory.register("book2", role="satellite")
    assert len(directory.discover_peers()) == 1

    monkeypatch.setenv("AGENT_DISPATCH_SATELLITE_GATE", "closed")
    fresh_runner = FederationRunner(directory, "book2", role="satellite")
    assert fresh_runner._registered is False

    state = fresh_runner.tick()
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
