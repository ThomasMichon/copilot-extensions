"""Tests for the ``session_deregister`` verb (agent-worktrees-authoritative-
daemon effort, Phase 3's eighth migrated call-site cluster -- the second
taken from a hot hook path, the sessionEnd counterpart to
``register_session``, PR #4159).

The extensive existing ``tracking.deregister_session`` test coverage
(``test_register_session.py``, ``test_session_lifecycle.py``,
``test_tracking.py``, ``test_handoff_cutover.py`` -- 400+ tests total)
already exercises this transaction end-to-end through the direct
in-process fallback path (the same code the verb now wraps), so this file
covers only what is new: the verb's own registration, one end-to-end proof
that ``tracking_write.dispatch`` reaches it via a real, live daemon, the
same boot-latency guarantee ``register_session`` established, and the
fsmonitor-stop-runs-after-the-lock-releases fix.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agent_worktrees import tracking, tracking_session_deregistration_write, tracking_write
from agent_worktrees.tracking import SessionEntry, WorktreeRecord, save_record


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path) -> Path:
    path = tmp_tracking_dir / "wt-dereg.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-dereg", branch="worktree/wt-dereg", worktree_path="/tmp/wt-dereg",
        repo="test-repo", machine="test", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=[SessionEntry(session_id="sess-1", started_at="2026-06-01T10:00:00")],
    )
    save_record(rec, path)
    return path


def test_importing_the_module_registers_the_verb():
    assert "session_deregister" in tracking_write.registered_verbs()


def test_apply_session_deregister_ends_the_matching_session(record_path):
    result = tracking_session_deregistration_write.apply_session_deregister(
        {"worktree_id": "wt-dereg", "yaml_path": str(record_path), "session_id": "sess-1"}
    )
    assert result == {"ok": True}
    record = tracking.load_record(record_path)
    entry = record.session_entry("sess-1")
    assert entry.ended_at is not None


def test_apply_session_deregister_is_a_noop_for_an_unknown_session(record_path):
    result = tracking_session_deregistration_write.apply_session_deregister(
        {"worktree_id": "wt-dereg", "yaml_path": str(record_path), "session_id": "sess-ghost"}
    )
    assert result == {"ok": True}
    record = tracking.load_record(record_path)
    assert record.session_entry("sess-1").ended_at is None


def test_stop_fsmonitor_daemon_runs_while_the_lock_is_still_held(
    record_path, monkeypatch,
):
    """(2026-09-27 PR #4238 review finding) A first migration attempt
    moved ``stop_fsmonitor_daemon`` outside the lock as a claimed
    scope-discipline improvement, but review correctly found the in-lock
    ordering is load-bearing, not incidental: holding the same lock
    ``register_session`` needs is what prevents a concurrent session
    start from adding a new open session in the window between this
    transaction's "no open sessions" check and the actual stop. Proves
    the call still runs while THIS verb's own lock is held, via a
    cross-THREAD probe (the in-process lock is per-thread reentrant, so a
    same-thread nested acquisition would trivially succeed and prove
    nothing) that attempts a separate, non-blocking ``_RecordLock`` on the
    same path from inside the fake ``stop_fsmonitor_daemon`` -- it must
    fail to acquire."""
    probe_result: dict = {}

    def _probe() -> None:
        with tracking._RecordLock(record_path, blocking=False) as lock:
            probe_result["acquired"] = lock.acquired

    def _fake_stop(worktree_path):
        probe_result["worktree_path"] = worktree_path
        probe_thread = threading.Thread(target=_probe)
        probe_thread.start()
        probe_thread.join(timeout=2)

    monkeypatch.setattr(
        tracking_session_deregistration_write, "stop_fsmonitor_daemon", _fake_stop,
    )
    result = tracking_session_deregistration_write.apply_session_deregister(
        {"worktree_id": "wt-dereg", "yaml_path": str(record_path), "session_id": "sess-1"}
    )
    assert result == {"ok": True}
    assert probe_result["worktree_path"] == "/tmp/wt-dereg"
    assert probe_result["acquired"] is False


def test_deregister_session_reaches_a_live_daemon(record_path, monkeypatch, monkeypatch_config):
    """End-to-end: proves ``tracking.deregister_session`` reaches
    ``apply_session_deregister`` via an actual ``CoalescingServer``."""
    from agent_worktrees import locks as _locks
    from agent_worktrees import status_monitor_runtime as _smr

    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        monkeypatch.setattr(_locks, "read_lock", lambda path: lock_data)
        monkeypatch.setattr(_smr, "_status_monitor_enabled", lambda: False)

        tracking.deregister_session("wt-dereg", "sess-1")
    finally:
        server.close()

    record = tracking.load_record(record_path)
    assert record.session_entry("sess-1").ended_at is not None


def test_deregister_session_never_waits_for_a_cold_daemon_boot(
    record_path, monkeypatch, monkeypatch_config,
):
    """The operative concern for this cluster (same as register_session's
    own PR #4159): a sessionEnd hook must never block on tracking_write's
    normal 4s boot-wait."""
    from agent_worktrees import locks as _locks
    from agent_worktrees import status_monitor_runtime as _smr

    ensure_monitor_calls = []

    def _spy_ensure_monitor() -> bool:
        ensure_monitor_calls.append(True)
        return False

    monkeypatch.setattr(_locks, "read_lock", lambda path: None)
    monkeypatch.setattr(_smr, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(_smr, "_ensure_status_monitor", _spy_ensure_monitor)

    started = time.time()
    tracking.deregister_session("wt-dereg", "sess-1")
    elapsed = time.time() - started

    assert elapsed < 1.0, (
        f"deregister_session took {elapsed:.2f}s -- must never approach "
        f"tracking_write.BOOT_WAIT_S ({tracking_write.BOOT_WAIT_S}s) on a "
        "sessionEnd hook"
    )
    assert ensure_monitor_calls == [True]
    record = tracking.load_record(record_path)
    assert record.session_entry("sess-1").ended_at is not None
