"""Tests for the provable-liveness lock-file primitive (#4272).

The shared mechanism of the session-state lattice: a JSON lock file whose
liveness is proved by PID + process start-time, so a crash (no teardown) leaves
a detectably stale lock rather than a false "alive". Covers the round-trip, the
start-time reuse guard, fail-open on an unprovable-but-alive owner, and
best-effort I/O (torn/absent files never raise).
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from agent_worktrees import locks


# ── process_start_time / _pid_alive on the live test process ──

def test_start_time_of_self_is_stable_token():
    t1 = locks.process_start_time(os.getpid())
    t2 = locks.process_start_time(os.getpid())
    assert t1 is not None and t1 == t2  # stable within a process


def test_start_time_of_dead_pid_is_none():
    # A pid that is essentially never live. Best-effort: None (can't read).
    assert locks.process_start_time(999_999_999) is None


def test_pid_alive_self_and_dead():
    assert locks._pid_alive(os.getpid()) is True
    assert locks._pid_alive(999_999_999) is False
    assert locks._pid_alive(0) is False
    assert locks._pid_alive(-1) is False


# ── write_lock / read_lock round-trip ──

def test_write_read_roundtrip(tmp_path):
    p = tmp_path / "mux.aaaa.lock"
    assert locks.write_lock(p, extra={"worktree_id": "aaaa"}) is True
    data = locks.read_lock(p)
    assert data is not None
    assert data["pid"] == os.getpid()
    assert data["schema"] == locks.LOCK_SCHEMA
    assert data["worktree_id"] == "aaaa"
    assert data["start_time"] == locks.process_start_time(os.getpid())
    assert isinstance(data["created_at"], (int, float))


def test_write_lock_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deep" / "bridge.sid.lock"
    assert locks.write_lock(p) is True
    assert p.exists()


def test_write_lock_atomic_replace_overwrites(tmp_path):
    p = tmp_path / "mux.aaaa.lock"
    locks.write_lock(p, pid=111, extra={"v": 1})
    locks.write_lock(p, pid=222, extra={"v": 2})
    data = locks.read_lock(p)
    assert data["pid"] == 222 and data["v"] == 2
    # No leftover temp files beside it.
    assert [f.name for f in tmp_path.iterdir()] == ["mux.aaaa.lock"]


def test_read_lock_absent_is_none(tmp_path):
    assert locks.read_lock(tmp_path / "nope.lock") is None


def test_read_lock_torn_is_none(tmp_path):
    p = tmp_path / "torn.lock"
    p.write_text("{not valid json", encoding="utf-8")
    assert locks.read_lock(p) is None


def test_read_lock_non_object_is_none(tmp_path):
    p = tmp_path / "arr.lock"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert locks.read_lock(p) is None


# ── lock_is_live: the provable-liveness core ──

def test_live_for_this_process(tmp_path):
    p = tmp_path / "self.lock"
    locks.write_lock(p)
    assert locks.lock_is_live(locks.read_lock(p)) is True
    assert locks.lock_live(p) is True


def test_dead_pid_is_not_live():
    data = {"pid": 999_999_999, "start_time": "123"}
    assert locks.lock_is_live(data) is False


def test_start_time_mismatch_is_stale_pid_reuse():
    """A live pid whose recorded start-time DIFFERS from the current one is a
    reused pid -> stale, even though the pid is alive."""
    data = {"pid": os.getpid(), "start_time": "definitely-not-the-real-token"}
    assert locks.lock_is_live(data) is False


def test_missing_recorded_start_time_fails_open_when_alive():
    """No reuse guard recorded: a live pid can't be disproved -> treated live."""
    data = {"pid": os.getpid()}  # no start_time
    assert locks.lock_is_live(data) is True


def test_unreadable_current_start_time_fails_open_when_alive():
    """Alive pid but current start-time unreadable -> can't disprove -> live."""
    data = {"pid": os.getpid(), "start_time": "123"}
    with patch("agent_worktrees.locks.process_start_time", return_value=None):
        assert locks.lock_is_live(data) is True


def test_non_dict_and_bad_pid_are_not_live():
    assert locks.lock_is_live(None) is False
    assert locks.lock_is_live({}) is False
    assert locks.lock_is_live({"pid": "notint", "start_time": "1"}) is False


# ── remove_lock ──

def test_remove_lock(tmp_path):
    p = tmp_path / "m.lock"
    locks.write_lock(p)
    assert p.exists()
    locks.remove_lock(p)
    assert not p.exists()
    locks.remove_lock(p)  # idempotent, no raise
    locks.remove_lock(tmp_path / "never.lock")  # absent, no raise


# ── crash simulation: a stale lock left by a dead owner reads not-live ──

def test_crashed_owner_leaves_detectably_stale_lock(tmp_path):
    """The whole point: an owner that died without teardown leaves a file whose
    recorded pid is dead -> lock_is_live False. (Simulated with a dead pid +
    a start-time token.)"""
    p = tmp_path / "mux.crashed.lock"
    payload = {"schema": locks.LOCK_SCHEMA, "pid": 999_999_999,
               "start_time": "555", "created_at": 1.0, "worktree_id": "crashed"}
    p.write_text(json.dumps(payload), encoding="utf-8")
    assert locks.lock_live(p) is False
