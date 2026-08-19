"""Tests for the finished-session auto-clean pass on the no-daemon cadence.

``sweep_finished_session_worktrees`` extends the prune-on-next-start cadence
(picker launch + session end) to ordinary (non-managed) worktrees whose work is
already landed -- finalized / merged / git-COMPLETED ones idle past the grace
window -- reusing the exact conservative safety of the manual ``cleanup``.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import tracking


def _rec(wt_id, *, status="finalized", path=None, kind="session",
         started="2026-06-01T10:00:00", resumed="2026-06-01T10:00:00",
         follow_up=False, owner_ref=None):
    rec = tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=path if path is not None else f"/no/such/dir-{wt_id}",
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at=started,
        last_resumed_at=resumed,
        resume_count=0,
        title=None,
        status=status,
        completed_at=None,
        sessions=[],
        prs=[],
        kind=kind,
    )
    rec.follow_up = follow_up
    rec.owner_ref = owner_ref
    return rec


class _FakeLock:
    """No-op stand-in for ``finalize.FinalizeLock``."""

    def __init__(self, *a, **k):
        pass

    def acquire(self):
        return None

    def release(self):
        return None


def _sweep(records, *, dry_run=True, mux=None, activity=None, now=None,
           active_sessions=None, turn_count=None, min_idle_secs=None,
           branch_merged=True, reaped=None):
    """Invoke ``sweep_finished_session_worktrees`` with all I/O patched.

    Records use non-existent paths so git-state resolves from ``status``
    (finalized -> COMPLETED), needing no real git. ``reaped`` (a list) captures
    the ids passed to ``_reap_worktree`` on a non-dry run.
    """
    repo = types.SimpleNamespace(anchor="/tmp/anchor", remote="origin",
                                 default_branch="main",
                                 worktree_root="/tmp/wt-root")
    config = types.SimpleNamespace(default_repo=repo, repo_name="repo")
    ctx = types.SimpleNamespace(active_sessions=(active_sessions or set()),
                                turn_count=(turn_count or {}))

    def _fake_reap(rec, info, r, tp):
        if reaped is not None:
            reaped.append(rec.worktree_id)
        return (0, [])

    with patch("agent_worktrees.config.load_config", return_value=config), \
         patch("agent_worktrees.config.tracking_dir", return_value=Path("/tmp")), \
         patch("agent_worktrees.tracking.list_records", return_value=records), \
         patch("agent_worktrees.sessions._list_mux_sessions",
               return_value=(mux or {})), \
         patch("agent_worktrees.sessions._mux_session_activity",
               return_value=(activity or {})), \
         patch("agent_worktrees.sessions.scan_sessions_fast", return_value=ctx), \
         patch("agent_worktrees.__main__._build_active_paths", return_value=set()), \
         patch("agent_worktrees.__main__._normalize_path", side_effect=lambda p: p), \
         patch("agent_worktrees.git_ops.is_branch_merged", return_value=branch_merged), \
         patch("agent_worktrees.git_ops.prune_worktrees", return_value=None), \
         patch("agent_worktrees.__main__._reap_worktree", side_effect=_fake_reap), \
         patch("agent_worktrees.finalize.FinalizeLock", _FakeLock):
        return cli.sweep_finished_session_worktrees(
            dry_run=dry_run, now=now, min_idle_secs=min_idle_secs)


def test_no_records_is_noop():
    assert _sweep([]) == {"removed": [], "skipped": []}


def test_reaps_finalized_session_dry_run():
    report = _sweep([_rec("done")])
    assert [x["id"] for x in report["removed"]] == ["done"]
    assert "would remove" in report["removed"][0]["reason"]


def test_ignores_managed_kinds():
    # system/bridge are handled by the managed sweep, never here.
    report = _sweep([_rec("brg", kind="bridge"), _rec("sys", kind="system")])
    assert report == {"removed": [], "skipped": []}


def test_spares_live_bound_session():
    rec = _rec("live")
    report = _sweep([rec], active_sessions={rec.worktree_path})
    assert report["removed"] == []
    assert {"id": "live", "reason": "live session"} in report["skipped"]


def test_spares_live_mux():
    report = _sweep([_rec("muxed")], mux={"wt-muxed": 0})
    assert report["removed"] == []
    assert {"id": "muxed", "reason": "live mux session"} in report["skipped"]


def test_idle_grace_not_elapsed_spares():
    # Fresh mux activity (idle 10s) under the default 48h grace -> spared.
    report = _sweep([_rec("fresh")], activity={"wt-fresh": 1000}, now=1010)
    assert report["removed"] == []
    assert {"id": "fresh", "reason": "idle grace not elapsed"} in report["skipped"]


def test_idle_grace_elapsed_collected():
    # Same worktree, idle well past the (overridden tiny) grace -> collected.
    report = _sweep([_rec("aged")], activity={"wt-aged": 1000}, now=1000 + 10_000,
                    min_idle_secs=60)
    assert [x["id"] for x in report["removed"]] == ["aged"]


def test_gone_unmerged_branch_spared():
    # A non-finalized worktree whose dir is gone and branch is NOT merged is
    # never collected (would lose un-landed work).
    report = _sweep([_rec("wip", status="active")], branch_merged=False)
    assert report["removed"] == []
    assert {"id": "wip", "reason": "branch unmerged (worktree dir missing)"} \
        in report["skipped"]


def test_gone_merged_branch_collected():
    report = _sweep([_rec("landed", status="active")], branch_merged=True)
    assert [x["id"] for x in report["removed"]] == ["landed"]


def test_non_dry_run_reaps_via_reap_worktree():
    reaped: list[str] = []
    report = _sweep([_rec("go")], dry_run=False, reaped=reaped)
    assert reaped == ["go"]
    assert [x["id"] for x in report["removed"]] == ["go"]


# --- kill-switch + grace resolution -----------------------------------------

def test_auto_clean_enabled_default(monkeypatch):
    monkeypatch.delenv(cli._NO_AUTO_CLEAN_ENV, raising=False)
    assert cli.auto_clean_enabled() is True


def test_auto_clean_kill_switch(monkeypatch):
    monkeypatch.setenv(cli._NO_AUTO_CLEAN_ENV, "1")
    assert cli.auto_clean_enabled() is False


def test_cadence_wrapper_respects_kill_switch(monkeypatch):
    monkeypatch.setenv(cli._NO_AUTO_CLEAN_ENV, "1")
    called = []
    monkeypatch.setattr(cli, "sweep_finished_session_worktrees",
                        lambda *a, **k: called.append(1) or {"removed": [], "skipped": []})
    cli._sweep_finished_sessions_on_cadence()
    assert called == []


def test_grace_env_override(monkeypatch):
    monkeypatch.setenv(cli._AUTO_CLEAN_GRACE_ENV, "120")
    assert cli._auto_clean_grace_secs() == 120.0


def test_grace_default_when_env_absent(monkeypatch):
    from agent_worktrees import gc as gc_mod
    monkeypatch.delenv(cli._AUTO_CLEAN_GRACE_ENV, raising=False)
    assert cli._auto_clean_grace_secs() == float(gc_mod.SESSION_GC_GRACE_SECS)


def test_grace_invalid_env_falls_back(monkeypatch):
    from agent_worktrees import gc as gc_mod
    monkeypatch.setenv(cli._AUTO_CLEAN_GRACE_ENV, "not-a-number")
    assert cli._auto_clean_grace_secs() == float(gc_mod.SESSION_GC_GRACE_SECS)
