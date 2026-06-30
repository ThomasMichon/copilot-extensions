"""Tests for the orphaned mux-session reaper (issue #713).

``reap_orphan_mux_sessions`` GCs leaked ``wt-<id>`` tmux/psmux sessions whose
worktree is finalized / gone / untracked, while conservatively sparing attached,
system, and still-active worktrees.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import tracking


def _rec(wt_id, *, status="active", path="/tmp/wt", kind="session"):
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
        status=status,
        completed_at=None,
        handoff_prompt=None,
        sessions=[],
        prs=[],
        kind=kind,
    )


def _run(sessions_map, records, *, dry_run=False):
    """Invoke the reaper with patched mux + tracking, capturing killed ids."""
    killed: list[str] = []

    def _kill(wt_id):
        killed.append(wt_id)
        return True

    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value=sessions_map), \
         patch("agent_worktrees.sessions.kill_tmux_session", side_effect=_kill), \
         patch("agent_worktrees.tracking.list_records", return_value=records), \
         patch("agent_worktrees.config.tracking_dir", return_value=Path("/tmp")):
        result = cli.reap_orphan_mux_sessions(dry_run=dry_run)
    return result, killed


class TestReapOrphans:
    def test_no_mux_available(self):
        result, killed = _run(None, [])
        assert result["available"] is False
        assert result["reaped"] == [] and killed == []

    def test_finalized_present_is_reaped(self, tmp_path):
        # dir exists but status finalized -> orphan
        rec = _rec("a", status="finalized", path=str(tmp_path))
        result, killed = _run({"wt-a": 0}, [rec])
        assert killed == ["a"]
        assert result["reaped"] == ["a"]

    def test_untracked_session_is_reaped(self):
        result, killed = _run({"wt-ghost": 0}, [])
        assert killed == ["ghost"]
        assert result["reaped"] == ["ghost"]

    def test_path_missing_is_reaped(self):
        rec = _rec("gone", status="active", path="/no/such/dir-xyz")
        result, killed = _run({"wt-gone": 0}, [rec])
        assert killed == ["gone"]
        assert result["reaped"] == ["gone"]

    def test_attached_is_spared(self, tmp_path):
        rec = _rec("a", status="finalized", path=str(tmp_path))
        result, killed = _run({"wt-a": 1}, [rec])
        assert killed == []
        assert {"id": "a", "reason": "attached"} in result["skipped"]

    def test_active_present_is_spared(self, tmp_path):
        rec = _rec("live", status="active", path=str(tmp_path))
        result, killed = _run({"wt-live": 0}, [rec])
        assert killed == []
        assert {"id": "live", "reason": "active"} in result["skipped"]

    def test_system_worktree_is_spared(self, tmp_path):
        rec = _rec("svc", status="finalized", path=str(tmp_path), kind="system")
        result, killed = _run({"wt-svc": 0}, [rec])
        assert killed == []
        assert {"id": "svc", "reason": "system"} in result["skipped"]

    def test_bridge_worktree_is_spared(self, tmp_path):
        rec = _rec("brg", status="finalized", path=str(tmp_path), kind="bridge")
        result, killed = _run({"wt-brg": 0}, [rec])
        assert killed == []
        assert {"id": "brg", "reason": "bridge"} in result["skipped"]

    def test_non_wt_sessions_ignored(self):
        result, killed = _run({"misc": 0, "scratch": 0}, [])
        assert killed == []
        assert result["reaped"] == [] and result["skipped"] == []

    def test_dry_run_kills_nothing(self, tmp_path):
        rec = _rec("a", status="finalized", path=str(tmp_path))
        result, killed = _run({"wt-a": 0}, [rec], dry_run=True)
        assert killed == []
        assert result["reaped"] == ["a"]

    def test_mixed_fleet(self, tmp_path):
        recs = [
            _rec("fin", status="finalized", path=str(tmp_path)),
            _rec("live", status="active", path=str(tmp_path)),
            _rec("sys", status="finalized", path=str(tmp_path), kind="system"),
        ]
        sessions_map = {
            "wt-fin": 0,    # reap
            "wt-live": 0,   # spare (active)
            "wt-sys": 0,    # spare (system)
            "wt-ghost": 0,  # reap (untracked)
            "wt-held": 2,   # spare (attached)
            "other": 0,     # ignore (not wt-)
        }
        result, killed = _run(sessions_map, recs)
        assert sorted(killed) == ["fin", "ghost"]
        assert sorted(result["reaped"]) == ["fin", "ghost"]
