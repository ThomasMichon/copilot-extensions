"""Tests for the ``session_register`` verb (agent-worktrees-authoritative-
daemon effort, Phase 3's seventh migrated call-site cluster -- the first
taken from the sessionStart-hook-critical path).

The extensive existing ``tracking.register_session`` test suites
(``test_register_session.py``, ``test_session_lifecycle.py``,
``test_tracking.py``, and others -- 700+ tests total) already exercise
every branch of this transaction end-to-end through the direct in-process
fallback path (the same code the verb now wraps), so this file does not
re-duplicate that coverage. It covers only what is new here: the verb's
own registration, one end-to-end proof that ``tracking_write.dispatch``
reaches it via a real, live daemon, and -- the operative concern for this
specific cluster -- that a cold/unreachable daemon never adds latency to
session registration, since ``register_session`` passes ``boot_wait_s=0``
precisely so a sessionStart hook is never made to wait on a daemon boot.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_worktrees import tracking, tracking_session_registration_write, tracking_write
from agent_worktrees.tracking import WorktreeRecord, save_record


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path) -> Path:
    path = tmp_tracking_dir / "wt-reg.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-reg", branch="worktree/wt-reg", worktree_path="/tmp/wt-reg",
        repo="test-repo", machine="test", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None, sessions=[],
    )
    save_record(rec, path)
    return path


def test_importing_the_module_registers_the_verb():
    assert "session_register" in tracking_write.registered_verbs()


def test_apply_session_register_creates_a_new_session_entry(record_path):
    result = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "wt-reg",
            "yaml_path": str(record_path),
            "session_id": "sess-1",
        }
    )
    assert result == {"ok": True, "linked_handoff": None}
    record = tracking.load_record(record_path)
    assert record.session_entry("sess-1") is not None


def test_apply_session_register_new_session_branch_feeds_claim_history(
    record_path,
):
    """worktree-claims-transitive-finalization Phase 3b: linking a
    handoff_token for a session NOT yet tracked (the "new session" branch)
    must feed claim_history for any ACTIVE pr-kind claim the worktree
    holds, after -- never before -- the verb's own durable save."""
    from agent_worktrees import claim_history, obligations

    record = tracking.load_record(record_path)
    record.sessions.append(tracking.SessionEntry("old", "2026-06-01T10:00:00"))
    record.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    tracking.open_handoff(record, "old", "tok", save=False)
    tracking.save_record(record, record_path)

    result = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "wt-reg",
            "yaml_path": str(record_path),
            "session_id": "new",
            "handoff_token": "tok",
        }
    )
    assert result["ok"] is True
    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]
    assert events[0]["session_id"] == "new"


def test_apply_session_register_existing_session_branch_feeds_claim_history(
    record_path,
):
    """Same Phase 3b wiring, for the "existing session" branch (the
    successor is already tracked, e.g. a pre-associated handoff
    candidate) -- the loop branch in apply_session_register, distinct from
    the new-SessionEntry branch covered above."""
    from agent_worktrees import claim_history, obligations

    record = tracking.load_record(record_path)
    record.sessions.append(tracking.SessionEntry("old", "2026-06-01T10:00:00"))
    record.sessions.append(tracking.SessionEntry("new", "2026-06-01T10:00:00"))
    record.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    tracking.open_handoff(record, "old", "tok", save=False)
    tracking.save_record(record, record_path)

    result = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "wt-reg",
            "yaml_path": str(record_path),
            "session_id": "new",
            "handoff_token": "tok",
        }
    )
    assert result["ok"] is True
    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]
    assert events[0]["session_id"] == "new"


def test_apply_session_register_rejects_a_terminal_managed_worktree(
    tmp_tracking_dir: Path,
):
    path = tmp_tracking_dir / "wt-terminal.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-terminal", branch="worktree/wt-terminal",
        worktree_path="/tmp/wt-terminal", repo="test-repo", machine="test",
        platform="wsl", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status="finalized", completed_at=None, sessions=[], kind="bridge",
    )
    save_record(rec, path)
    result = tracking_session_registration_write.apply_session_register(
        {"worktree_id": "wt-terminal", "yaml_path": str(path), "session_id": "sess-1"}
    )
    assert result["error"] == "lifecycle"
    record = tracking.load_record(path)
    assert record.session_entry("sess-1") is None


def test_register_session_reaches_a_live_daemon(record_path, monkeypatch, monkeypatch_config):
    """End-to-end: proves ``tracking.register_session`` reaches
    ``apply_session_register`` via an actual ``CoalescingServer`` (the
    two-process-shaped path real production traffic will use)."""
    from agent_worktrees import locks as _locks
    from agent_worktrees import status_monitor_runtime as _smr

    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        monkeypatch.setattr(_locks, "read_lock", lambda path: lock_data)
        monkeypatch.setattr(_smr, "_status_monitor_enabled", lambda: False)

        linked = tracking.register_session("wt-reg", "sess-daemon", pid=123)
    finally:
        server.close()

    assert linked is None
    record = tracking.load_record(record_path)
    entry = record.session_entry("sess-daemon")
    assert entry is not None
    assert entry.pid == 123


def test_register_session_never_waits_for_a_cold_daemon_boot(
    record_path, monkeypatch, monkeypatch_config,
):
    """The operative concern for this cluster (operator direction,
    2026-09-27): a sessionStart hook must never block on
    ``tracking_write``'s normal 4s boot-wait. Simulates the cold/
    unreachable-daemon case (no rendezvous data at all) with
    ``ensure_monitor`` wired to a real, timed spy -- proving the call
    returns near-instantly rather than waiting anywhere close to
    ``tracking_write.BOOT_WAIT_S``."""
    from agent_worktrees import locks as _locks
    from agent_worktrees import status_monitor_runtime as _smr

    ensure_monitor_calls = []

    def _spy_ensure_monitor() -> bool:
        ensure_monitor_calls.append(True)
        return False  # simulate: spawn attempted, still not up yet

    monkeypatch.setattr(_locks, "read_lock", lambda path: None)
    monkeypatch.setattr(_smr, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(_smr, "_ensure_status_monitor", _spy_ensure_monitor)

    started = time.time()
    linked = tracking.register_session("wt-reg", "sess-cold", pid=456)
    elapsed = time.time() - started

    assert linked is None
    assert elapsed < 1.0, (
        f"register_session took {elapsed:.2f}s -- must never approach "
        f"tracking_write.BOOT_WAIT_S ({tracking_write.BOOT_WAIT_S}s) on a "
        "sessionStart hook"
    )
    # ensure_monitor is still called (fire-and-forget: warms the daemon for
    # the NEXT session) -- boot_wait_s=0 only skips the spin-wait, not the
    # spawn attempt itself.
    assert ensure_monitor_calls == [True]
    record = tracking.load_record(record_path)
    entry = record.session_entry("sess-cold")
    assert entry is not None
    assert entry.pid == 456
