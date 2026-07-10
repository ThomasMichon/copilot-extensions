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
        sessions=[],
        prs=[],
        kind=kind,
    )


def _run(sessions_map, records, *, dry_run=False, only_id=None,
         activity=None, now=None):
    """Invoke the reaper with patched mux + tracking, capturing killed ids.

    ``activity`` maps session_name -> last-activity epoch (defaults every session
    to epoch 0, i.e. long-idle, so predicate tests reap as before); pass recent
    timestamps to exercise the #713 busy-spare gate.
    """
    killed: list[str] = []

    def _kill(wt_id):
        killed.append(wt_id)
        return True

    act = ({name: 0 for name in (sessions_map or {})}
           if activity is None else activity)

    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value=sessions_map), \
         patch("agent_worktrees.sessions._mux_session_activity",
               return_value=act), \
         patch("agent_worktrees.sessions.kill_tmux_session", side_effect=_kill), \
         patch("agent_worktrees.tracking.list_records", return_value=records), \
         patch("agent_worktrees.config.tracking_dir", return_value=Path("/tmp")):
        result = cli.reap_orphan_mux_sessions(
            dry_run=dry_run, only_id=only_id, now=now)
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

    # ── #713 prevention: targeted single-worktree reap (only_id) ──────────────

    def test_only_id_targets_one_orphan(self, tmp_path):
        recs = [
            _rec("fin", status="finalized", path=str(tmp_path)),
            _rec("fin2", status="finalized", path=str(tmp_path)),
        ]
        result, killed = _run({"wt-fin": 0, "wt-fin2": 0}, recs, only_id="fin")
        assert killed == ["fin"]                 # only the targeted id
        assert result["reaped"] == ["fin"]

    def test_only_id_still_spares_active(self, tmp_path):
        """The targeted reap applies the SAME predicate: an active worktree is
        spared even when named explicitly (the core #713 safety guarantee)."""
        rec = _rec("live", status="active", path=str(tmp_path))
        result, killed = _run({"wt-live": 0}, [rec], only_id="live")
        assert killed == []
        assert {"id": "live", "reason": "active"} in result["skipped"]

    def test_only_id_still_spares_attached(self, tmp_path):
        rec = _rec("held", status="finalized", path=str(tmp_path))
        result, killed = _run({"wt-held": 1}, [rec], only_id="held")
        assert killed == []
        assert {"id": "held", "reason": "attached"} in result["skipped"]

    def test_only_id_no_match_is_noop(self, tmp_path):
        rec = _rec("fin", status="finalized", path=str(tmp_path))
        result, killed = _run({"wt-fin": 0}, [rec], only_id="nope")
        assert killed == []
        assert result["reaped"] == [] and result["skipped"] == []

    # ── #713 idle gate: never reap a busy (recently-active) session ───────────

    def test_busy_finalized_is_spared(self, tmp_path):
        """A finalized session with fresh pane activity (Copilot still working)
        is spared, even unattended -- closing a tab preserves a live session."""
        now = 1_000_000.0
        rec = _rec("fin", status="finalized", path=str(tmp_path))
        result, killed = _run(
            {"wt-fin": 0}, [rec], activity={"wt-fin": now - 60}, now=now)
        assert killed == []
        assert {"id": "fin", "reason": "busy"} in result["skipped"]

    def test_idle_finalized_past_grace_is_reaped(self, tmp_path):
        """Once quiet past the 6h grace window, the finalized orphan is reaped."""
        now = 1_000_000.0
        rec = _rec("fin", status="finalized", path=str(tmp_path))
        result, killed = _run(
            {"wt-fin": 0}, [rec],
            activity={"wt-fin": now - 7 * 3600}, now=now)
        assert killed == ["fin"]
        assert result["reaped"] == ["fin"]

    def test_only_id_spares_busy(self, tmp_path):
        now = 1_000_000.0
        rec = _rec("fin", status="finalized", path=str(tmp_path))
        result, killed = _run(
            {"wt-fin": 0}, [rec], only_id="fin",
            activity={"wt-fin": now - 30}, now=now)
        assert killed == []
        assert {"id": "fin", "reason": "busy"} in result["skipped"]

    def test_activity_unknown_is_spared(self):
        """No activity signal at all -> spare (never risk killing a busy one)."""
        result, killed = _run({"wt-ghost": 0}, [], activity={})
        assert killed == []
        assert {"id": "ghost", "reason": "activity-unknown"} in result["skipped"]

    def test_activity_falls_back_to_tracking_timestamp(self, tmp_path):
        """When the mux reports no activity for the session, the tracking
        record's last-resumed time is used (here: long ago -> reaped)."""
        rec = _rec("fin", status="finalized", path=str(tmp_path))  # 2026-06-01
        result, killed = _run({"wt-fin": 0}, [rec], activity={})
        assert killed == ["fin"]


# ── #2149/#713 session-end sweep: post-exit reaps idle orphans, no daemon ─────

def test_sweep_orphans_on_exit_is_best_effort(monkeypatch):
    """A reap hiccup at session end never propagates out of post-exit."""
    def boom():
        raise RuntimeError("mux enumeration failed")

    monkeypatch.setattr(cli, "reap_orphan_mux_sessions", boom)
    assert cli._sweep_orphans_on_exit() is None      # swallowed, no raise


def test_sweep_orphans_on_exit_runs_the_reaper(monkeypatch):
    seen = {"n": 0}
    monkeypatch.setattr(
        cli, "reap_orphan_mux_sessions",
        lambda: (seen.__setitem__("n", seen["n"] + 1)
                 or {"available": True, "reaped": [], "skipped": [], "errors": []}))
    cli._sweep_orphans_on_exit()
    assert seen["n"] == 1


def _post_exit_args(wt_id="wt-x"):
    import types
    return types.SimpleNamespace(worktree_id=wt_id)


def test_post_exit_sweeps_orphans_when_finalized(tmp_path, monkeypatch):
    """The 'already finalized' path still triggers the session-end sweep."""
    calls = {"sweep": 0}
    monkeypatch.setattr(cli, "_sweep_orphans_on_exit",
                        lambda: calls.__setitem__("sweep", calls["sweep"] + 1))
    monkeypatch.setattr(cli.cfg, "load_config", lambda *a, **k: object())
    monkeypatch.setattr(cli, "_infer_worktree_id", lambda wid, config: "wt-x")
    monkeypatch.setattr(cli, "_resolve_worktree_id", lambda wid: "wt-x")
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: tmp_path)
    (tmp_path / "wt-x.yaml").write_text("")   # record exists
    monkeypatch.setattr(cli.tracking, "load_record",
                        lambda p: _rec("wt-x", status="finalized"))

    assert cli.cmd_post_exit(_post_exit_args()) == 0
    assert calls["sweep"] == 1


def test_post_exit_sweeps_orphans_when_no_record(tmp_path, monkeypatch):
    """A session end for an untracked worktree still sweeps other orphans."""
    calls = {"sweep": 0}
    monkeypatch.setattr(cli, "_sweep_orphans_on_exit",
                        lambda: calls.__setitem__("sweep", calls["sweep"] + 1))
    monkeypatch.setattr(cli.cfg, "load_config", lambda *a, **k: object())
    monkeypatch.setattr(cli, "_infer_worktree_id", lambda wid, config: "wt-x")
    monkeypatch.setattr(cli, "_resolve_worktree_id", lambda wid: "wt-x")
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: tmp_path)   # no yaml -> missing

    assert cli.cmd_post_exit(_post_exit_args()) == 0
    assert calls["sweep"] == 1
