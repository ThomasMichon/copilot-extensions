"""Tests for ``_build_active_paths`` mux liveness (picker Active-section signal).

``_build_active_paths`` must derive the set of worktree paths with a live mux
session from a SINGLE batched ``list-sessions`` snapshot on the hot path (one
subprocess for the whole fleet) rather than a ``has-session`` probe per
worktree. It degrades to the per-worktree probe only when the batch list is
unavailable (mux missing/blocked). See __main__._build_active_paths.
"""

from __future__ import annotations

import types
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import tracking


def _rec(wt_id, *, path):
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=path,
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        prs=[],
        kind="session",
    )


def _empty_ctx():
    return types.SimpleNamespace(active_sessions={})


def test_active_paths_use_single_batched_list():
    """One ``_list_mux_sessions`` call classifies the whole fleet; the
    per-worktree ``has_mux_session`` probe is NOT used on the batch path."""
    recs = [_rec("aaaa", path="/tmp/a"), _rec("bbbb", path="/tmp/b"),
            _rec("cccc", path="/tmp/c")]
    batch = {"wt-aaaa": 1, "wt-cccc": 0}  # b has no live session

    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value=batch) as m_list, \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        active = cli._build_active_paths(recs, session_ctx=_empty_ctx())

    assert active == {"/tmp/a", "/tmp/c"}
    m_list.assert_called_once()
    m_has.assert_not_called()  # batch path must not fall back to per-worktree


def test_active_paths_fall_back_when_batch_unavailable():
    """When ``_list_mux_sessions`` returns None (mux missing/blocked), degrade
    to the per-worktree ``has_mux_session`` probe rather than lose liveness."""
    recs = [_rec("aaaa", path="/tmp/a"), _rec("bbbb", path="/tmp/b")]

    def _has(wt_id):
        return wt_id == "aaaa"

    with patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session", side_effect=_has):
        active = cli._build_active_paths(recs, session_ctx=_empty_ctx())

    assert active == {"/tmp/a"}


def test_active_paths_union_lock_and_mux():
    """Lock-file sessions and mux sessions both contribute; the result is their
    union (a worktree live by either signal is active)."""
    recs = [_rec("aaaa", path="/tmp/a"), _rec("bbbb", path="/tmp/b")]
    ctx = types.SimpleNamespace(active_sessions={"/tmp/b": ["sid-1"]})

    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value={"wt-aaaa": 0}), \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        active = cli._build_active_paths(recs, session_ctx=ctx)

    assert active == {"/tmp/a", "/tmp/b"}
    m_has.assert_not_called()


def test_active_paths_include_hosted_session_bindings():
    rec = _rec("aaaa", path="/tmp/a")
    rec.session_backend = tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        created_at="2026-09-03T00:00:00+00:00",
        last_seen_at="2026-09-03T00:00:00+00:00",
    )

    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}):
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())

    assert active == {"/tmp/a"}
