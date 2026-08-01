"""Tests for ``sessions.verify_worktree_active`` (issue #4057).

The authoritative single-worktree liveness verdict used at the Actions-menu /
Enter moments. Unlike the batched populate scan it combines mux presence with
the cwd-independent ``inuse.<pid>.lock`` binding (via reclaim), so it also
catches a **bare** (un-muxed) bound Copilot the mux fleet view cannot see.
"""

from __future__ import annotations

import types
from unittest.mock import patch

from agent_worktrees import sessions


def _rec(wt_id="aaaa"):
    return types.SimpleNamespace(worktree_id=wt_id, worktree_path="/tmp/wt")


def _bound(sid, homing):
    return {"session_id": sid, "pid": 123, "cwd": "/tmp/wt",
            "worktree_id": "aaaa", "homing": homing}


def test_mux_only_is_active_source_mux():
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=True, clients=1)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots", return_value=[]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True
    assert v.mux_live is True and v.mux_clients == 1
    assert v.live_session_ids == [] and v.bare is False
    assert v.source == "mux"


def test_bare_lock_only_is_active_source_lock():
    """A bare bound Copilot with no mux is still ACTIVE (the case the mux fleet
    view misses); source is 'lock' and bare is flagged."""
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=False, clients=0)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-1", "bare")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True
    assert v.mux_live is False
    assert v.live_session_ids == ["sid-1"]
    assert v.bare is True
    assert v.source == "lock"


def test_both_signals():
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=True, clients=2)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-1", "mux")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True and v.source == "both"
    assert v.mux_live is True and v.live_session_ids == ["sid-1"]
    assert v.bare is False


def test_nothing_live_is_inactive():
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=False, clients=0)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots", return_value=[]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is False and v.source == "none"
    assert v.mux_live is False and v.live_session_ids == []


def test_dedupes_and_sorts_session_ids():
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=False)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-2", "bare"), _bound("sid-1", "mux"),
                             _bound("sid-2", "bare")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.live_session_ids == ["sid-1", "sid-2"]
    assert v.bare is True  # at least one bare binding present


def test_degrades_when_reclaim_raises():
    """A reclaim hiccup never crashes the verify; mux liveness still counts."""
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=True, clients=0)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               side_effect=RuntimeError("boom")):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True and v.mux_live is True
    assert v.live_session_ids == [] and v.source == "mux"


def test_degrades_when_mux_raises():
    with patch("agent_worktrees.sessions.mux_status_many",
               side_effect=OSError("blocked")), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-1", "bare")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True and v.mux_live is False
    assert v.source == "lock" and v.bare is True
