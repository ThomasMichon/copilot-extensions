"""Tests for the bridge-lock layer of the session-state lattice (#4272).

Covers the producer CLI (`agent-worktrees session-lock write/remove` ->
`cmd_session_lock`), the file-first reader (`reclaim.live_bridge_worktrees`),
and the populate consume (`_worktree_to_dict` -> `session_bridge_live` ->
`derive._state` fast-pass; `_build_active_paths` union on the classify path).

A bridge-owned Copilot (cwd=home, #1416) is invisible to the mux + registered-
session scans; its `bridge.lock` -- a provable-liveness marker carrying the bound
worktree id -- makes it cheaply ACTIVE from a stat sweep, cwd-independently.
"""

from __future__ import annotations

import argparse
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import locks, reclaim


# ── cmd_session_lock: write / remove into the session-state dir ──

def _run_session_lock(tmp_path, **kw):
    args = argparse.Namespace(
        action=kw.get("action", "write"),
        session=kw.get("session", "sid-aaaa"),
        worktree=kw.get("worktree", "wt-aaaa"),
        pid=kw.get("pid", os.getpid()),
        kind=kw.get("kind", "bridge"),
        json=kw.get("json", False),
    )
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        return args, cli.cmd_session_lock(args)


def test_session_lock_write_creates_provable_lock(tmp_path):
    args, rc = _run_session_lock(tmp_path)
    assert rc == 0
    lock_path = tmp_path / "sid-aaaa" / "bridge.lock"
    assert lock_path.exists()
    data = locks.read_lock(lock_path)
    assert data["worktree_id"] == "wt-aaaa"
    assert data["session_id"] == "sid-aaaa"
    assert data["kind"] == "bridge"
    assert data["pid"] == os.getpid()
    assert data["start_time"] == locks.process_start_time(os.getpid())
    # This process is alive with a matching start-time -> provably live.
    assert locks.lock_is_live(data) is True


def test_session_lock_remove_clears_it(tmp_path):
    _run_session_lock(tmp_path, action="write")
    lock_path = tmp_path / "sid-aaaa" / "bridge.lock"
    assert lock_path.exists()
    _, rc = _run_session_lock(tmp_path, action="remove")
    assert rc == 0
    assert not lock_path.exists()


def test_session_lock_remove_is_idempotent(tmp_path):
    _, rc = _run_session_lock(tmp_path, action="remove", session="never")
    assert rc == 0  # no lock present, no raise


def test_session_lock_json_output(tmp_path, capsys):
    _run_session_lock(tmp_path, json=True)
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True and out["action"] == "write"
    assert out["worktree_id"] == "wt-aaaa"


def test_session_lock_is_no_project_command():
    # Producers (agent-bridge) invoke it without project context.
    assert "session-lock" in cli._NO_PROJECT_COMMANDS


# ── reclaim.live_bridge_worktrees: file-first read ──

def test_live_bridge_worktrees_reads_live_lock(tmp_path):
    d = tmp_path / "sid-aaaa"
    locks.write_lock(d / "bridge.lock", extra={"worktree_id": "wt-aaaa"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.live_bridge_worktrees() == {"wt-aaaa"}


def test_live_bridge_worktrees_skips_stale_lock(tmp_path):
    d = tmp_path / "sid-dead"
    payload = {"schema": locks.LOCK_SCHEMA, "pid": 999_999_999,
               "start_time": "1", "worktree_id": "wt-dead"}
    (d).mkdir()
    (d / "bridge.lock").write_text(json.dumps(payload), encoding="utf-8")
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.live_bridge_worktrees() == set()  # dead owner -> skipped


def test_live_bridge_worktrees_ignores_lock_without_worktree(tmp_path):
    d = tmp_path / "sid-nowt"
    locks.write_lock(d / "bridge.lock")  # no worktree_id recorded
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.live_bridge_worktrees() == set()


def test_live_bridge_worktrees_empty_when_no_state_dir(tmp_path):
    missing = tmp_path / "does-not-exist"
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=missing):
        assert reclaim.live_bridge_worktrees() == set()


def test_live_bridge_worktrees_multiple(tmp_path):
    locks.write_lock(tmp_path / "s1" / "bridge.lock", extra={"worktree_id": "a"})
    locks.write_lock(tmp_path / "s2" / "bridge.lock", extra={"worktree_id": "b"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.live_bridge_worktrees() == {"a", "b"}


# ── reclaim.resolve_bridge_bound: bridge.lock -> reap targets (#4272) ──

def test_resolve_bridge_bound_returns_live_owner_target(tmp_path):
    # A live bridge.lock for the worktree -> a reap target carrying its pid +
    # the lock path, so Reclaim can reach a cwd=home bridge session.
    d = tmp_path / "sid-live"
    locks.write_lock(d / "bridge.lock", extra={"worktree_id": "wt-aaaa"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path), \
         patch("agent_worktrees.reclaim.homing_of", return_value="bare"):
        out = reclaim.resolve_bridge_bound("wt-aaaa", table={})
    assert len(out) == 1
    t = out[0]
    assert t["pid"] == os.getpid() and t["worktree_id"] == "wt-aaaa"
    assert t["session_id"] == "sid-live"
    assert t["bridge_lock"].endswith("bridge.lock")


def test_resolve_bridge_bound_skips_other_worktrees(tmp_path):
    locks.write_lock(tmp_path / "s1" / "bridge.lock", extra={"worktree_id": "other"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.resolve_bridge_bound("wt-aaaa", table={}) == []


def test_resolve_bridge_bound_skips_stale_lock(tmp_path):
    d = tmp_path / "sid-dead"
    payload = {"schema": locks.LOCK_SCHEMA, "pid": 999_999_999,
               "start_time": "1", "worktree_id": "wt-aaaa"}
    d.mkdir()
    (d / "bridge.lock").write_text(json.dumps(payload), encoding="utf-8")
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.resolve_bridge_bound("wt-aaaa", table={}) == []


def test_resolve_bridge_bound_empty_worktree_id_is_noop(tmp_path):
    locks.write_lock(tmp_path / "s1" / "bridge.lock", extra={"worktree_id": "x"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path):
        assert reclaim.resolve_bridge_bound("", table={}) == []


# ── reclaim.clear_bridge_locks: post-reap residue removal ──

def test_clear_bridge_locks_removes_forced_pid(tmp_path):
    # After Reclaim force-kills the owner, its bridge.lock is unlinked even though
    # the pid (this live process) is still a "live Copilot" by the raw check.
    d = tmp_path / "sid-forced"
    locks.write_lock(d / "bridge.lock", extra={"worktree_id": "wt-aaaa"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path), \
         patch("agent_worktrees.sessions._is_copilot_process", return_value=True), \
         patch("agent_worktrees.sessions._is_process_alive", return_value=True):
        removed = reclaim.clear_bridge_locks(
            "wt-aaaa", force_pids={os.getpid()}, table={})
    assert len(removed) == 1 and removed[0]["session_id"] == "sid-forced"
    assert not (d / "bridge.lock").exists()


def test_clear_bridge_locks_keeps_live_owner(tmp_path):
    # A still-live bridge owner (not force-killed) -> lock preserved.
    d = tmp_path / "sid-live"
    locks.write_lock(d / "bridge.lock", extra={"worktree_id": "wt-aaaa"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path), \
         patch("agent_worktrees.sessions._is_copilot_process", return_value=True), \
         patch("agent_worktrees.sessions._is_process_alive", return_value=True):
        removed = reclaim.clear_bridge_locks("wt-aaaa", force_pids=set(), table={})
    assert removed == []
    assert (d / "bridge.lock").exists()


def test_clear_bridge_locks_removes_dead_owner(tmp_path):
    # A crashed owner (pid not a live Copilot) -> stale lock unlinked.
    d = tmp_path / "sid-dead"
    locks.write_lock(d / "bridge.lock", extra={"worktree_id": "wt-aaaa"})
    with patch("agent_worktrees.sessions._session_state_dir",
               return_value=tmp_path), \
         patch("agent_worktrees.sessions._is_copilot_process", return_value=False):
        removed = reclaim.clear_bridge_locks("wt-aaaa", force_pids=set(), table={})
    assert len(removed) == 1
    assert not (d / "bridge.lock").exists()


# ── Populate consume: _worktree_to_dict + _build_active_paths ──

def _rec(wt_id="wt-aaaa", path="/tmp/a"):
    from agent_worktrees import tracking
    return tracking.WorktreeRecord(
        worktree_id=wt_id, branch=f"worktree/{wt_id}", worktree_path=path,
        repo="o/r", machine="m", platform="wsl",
        started_at="2026-08-03T10:00:00", last_resumed_at="2026-08-03T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=[], prs=[], kind="session",
    )


def test_worktree_to_dict_emits_bridge_live_flag():
    rec = _rec()
    d = cli._worktree_to_dict(rec, bridge_live_wts={"wt-aaaa"})
    assert d.get("session_bridge_live") is True


def test_worktree_to_dict_omits_bridge_live_when_absent():
    rec = _rec()
    d = cli._worktree_to_dict(rec, bridge_live_wts=set())
    assert "session_bridge_live" not in d


def test_build_active_paths_unions_bridge_live(tmp_path):
    rec = _rec("wt-aaaa", "/tmp/a")
    ctx = SimpleNamespace(active_sessions={})
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False), \
         patch("agent_worktrees.reclaim.live_bridge_worktrees",
               return_value={"wt-aaaa"}):
        active = cli._build_active_paths([rec], session_ctx=ctx)
    assert active == {cli._normalize_path("/tmp/a")}


def test_build_active_paths_no_bridge_is_noop(tmp_path):
    rec = _rec("wt-aaaa", "/tmp/a")
    ctx = SimpleNamespace(active_sessions={})
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False), \
         patch("agent_worktrees.reclaim.live_bridge_worktrees",
               return_value=set()):
        active = cli._build_active_paths([rec], session_ctx=ctx)
    assert active == set()
