"""Tests for the finished-session auto-clean pass on the no-daemon cadence.

``sweep_finished_session_worktrees`` extends the prune-on-next-start cadence
(picker launch + session end) to ordinary (non-managed) worktrees whose work is
already landed -- finalized / merged / git-COMPLETED ones idle past the grace
window -- reusing the exact conservative safety of the manual ``cleanup``.
"""

from __future__ import annotations

import argparse
import types
from pathlib import Path
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import cleanup_gc_cli
from agent_worktrees import output
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


class _FakeRecordLock:
    """No-op stand-in for ``tracking._RecordLock`` (context manager)."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sweep(records, *, dry_run=True, mux=None, activity=None, now=None,
           active_sessions=None, turn_count=None, min_idle_secs=None,
           branch_merged=True, reaped=None, scanned=None):
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
    by_id = {r.worktree_id: r for r in records}

    def _fake_reap(rec, info, r, tp):
        if reaped is not None:
            reaped.append(rec.worktree_id)
        return (0, [])

    def _fake_load_record(path):
        return by_id[Path(path).stem]

    def _fake_scan(selected):
        if scanned is not None:
            scanned.append([rec.worktree_id for rec in selected])
        return ctx

    real_exists = Path.exists

    def _fake_exists(self):
        # yaml tracking-record paths under the fake tracking dir "exist" (the
        # test never writes real YAML files); everything else (worktree dirs)
        # keeps real filesystem behavior so git-state still resolves from
        # `status` alone, per this helper's docstring.
        if self.parent == Path("/tmp") and self.suffix == ".yaml":
            return True
        return real_exists(self)

    with patch("agent_worktrees.config.load_config", return_value=config), \
         patch("agent_worktrees.config.tracking_dir", return_value=Path("/tmp")), \
         patch("agent_worktrees.tracking.list_records", return_value=records), \
         patch("agent_worktrees.tracking.load_record", side_effect=_fake_load_record), \
         patch("agent_worktrees.tracking._RecordLock", _FakeRecordLock), \
         patch("pathlib.Path.exists", _fake_exists), \
         patch("agent_worktrees.sessions._list_mux_sessions",
               return_value=(mux or {})), \
         patch("agent_worktrees.sessions._mux_session_activity",
               return_value=(activity or {})), \
         patch("agent_worktrees.sessions.scan_sessions_fast", side_effect=_fake_scan), \
         patch("agent_worktrees.__main__._build_active_paths", return_value=set()), \
         patch("agent_worktrees.sessions._normalize_path", side_effect=lambda p: p), \
         patch("agent_worktrees.__main__._hosted_session_blocks_cleanup",
               return_value=False), \
         patch("agent_worktrees.git_ops.is_branch_merged", return_value=branch_merged), \
         patch("agent_worktrees.git_ops.prune_worktrees", return_value=None), \
         patch("agent_worktrees.__main__._reap_worktree", side_effect=_fake_reap), \
         patch("agent_worktrees.finalize.FinalizeLock", _FakeLock):
        return cli.sweep_finished_session_worktrees(
            dry_run=dry_run, now=now, min_idle_secs=min_idle_secs)


def test_no_records_is_noop():
    assert _sweep([]) == {"removed": [], "skipped": []}


def _tombstone(wt_id, *, paired=False):
    rec = _rec(wt_id)
    rec.reaped_at = "2026-06-02T10:00:00"
    if paired:
        rec.pair_id = f"pair-{wt_id}"
        rec.pair_kind = "worktree"
        rec.pair_role = "harness"
        rec.pair_ref = f"m/knowledge/{wt_id}-k"
    return rec


def test_retained_tombstone_history_does_not_repeat_cleanup():
    records = [_tombstone(f"done-{i}") for i in range(128)]
    records.append(_rec("pending"))
    scanned, reaped = [], []
    report = _sweep(records, dry_run=False, scanned=scanned, reaped=reaped)
    assert reaped == ["pending"]
    assert scanned == [["pending"], ["pending"]]
    assert [item["id"] for item in report["removed"]] == ["pending"]


def test_archived_history_is_not_scanned_or_reaped():
    scanned, reaped = [], []
    assert _sweep(
        [_rec("archived", status="archived")],
        dry_run=False, scanned=scanned, reaped=reaped,
    ) == {"removed": [], "skipped": []}
    assert scanned == reaped == []


def test_reaped_pair_without_retired_sibling_is_not_scanned():
    rec = _tombstone("paired", paired=True)
    for sibling in (
        None,
        types.SimpleNamespace(status="active", reaped_at=None),
        types.SimpleNamespace(status="finalized", reaped_at=None),
    ):
        scanned, reaped = [], []
        with patch("agent_worktrees.paired_claims.reciprocal_sibling", return_value=sibling):
            assert _sweep(
                [rec], dry_run=False, scanned=scanned, reaped=reaped,
            ) == {"removed": [], "skipped": []}
        assert scanned == reaped == []


def test_retained_paired_history_only_reads_sibling_once_per_record():
    records = [_tombstone(f"paired-{i}", paired=True) for i in range(128)]
    scanned, reaped = [], []
    with patch(
        "agent_worktrees.paired_claims.reciprocal_sibling", return_value=None,
    ) as sibling:
        assert _sweep(
            records, dry_run=False, scanned=scanned, reaped=reaped,
        ) == {"removed": [], "skipped": []}
    assert sibling.call_count == len(records)
    assert scanned == reaped == []


def test_tombstone_selection_reads_current_reciprocal_records(tmp_path, monkeypatch):
    from agent_worktrees import reap_cli

    monkeypatch.setattr(cli.cfg, "project_dir", lambda project: tmp_path / project)
    rec = _tombstone("parent", paired=True)
    rec.repo = "harness"
    assert not reap_cli._needs_session_cleanup(rec)

    sibling = _rec("parent-k")
    sibling.repo = "knowledge"
    sibling.pair_id = rec.pair_id
    sibling.pair_kind = "worktree"
    sibling.pair_role = "knowledge"
    sibling.pair_ref = "m/harness/parent"
    path = tmp_path / "knowledge" / "worktrees" / "parent-k.yaml"
    tracking.save_record(sibling, path)
    assert not reap_cli._needs_session_cleanup(rec)

    sibling.reaped_at = rec.reaped_at
    tracking.save_record(sibling, path)
    assert reap_cli._needs_session_cleanup(rec)

    rec.pair_id = "different-pair"
    assert not reap_cli._needs_session_cleanup(rec)


def test_both_reaped_pair_still_reaches_locked_cleanup():
    rec = _tombstone("paired", paired=True)
    sibling = types.SimpleNamespace(status="finalized", reaped_at=rec.reaped_at)
    reaped = []
    with patch("agent_worktrees.paired_claims.reciprocal_sibling", return_value=sibling):
        report = _sweep([rec], dry_run=False, reaped=reaped)
    assert reaped == ["paired"]
    assert [item["id"] for item in report["removed"]] == ["paired"]


def test_reaped_marker_does_not_skip_recreated_checkout(tmp_path):
    rec = _tombstone("recreated")
    rec.worktree_path = str(tmp_path)
    with patch("agent_worktrees.git_ops.classify_worktree") as classify:
        classify.return_value = cli.git_ops.WorktreeStateInfo(
            state=cli.git_ops.WorktreeState.ACTIVE,
        )
        report = _sweep([rec])
    classify.assert_called_once()
    assert report["removed"] == []


def test_reaped_marker_does_not_skip_reopened_record():
    rec = _tombstone("reopened")
    rec.status = "active"
    scanned = []
    _sweep([rec], scanned=scanned)
    assert scanned == [["reopened"]]


def test_cadence_cleanup_output_is_bounded(monkeypatch, capsys):
    monkeypatch.setattr(cli, "auto_clean_enabled", lambda: True)
    monkeypatch.setattr(cli, "sweep_finished_session_worktrees", lambda: {
        "removed": [{"id": f"finished-{index}"} for index in range(100)],
        "skipped": [],
    })
    cli._sweep_finished_sessions_on_cadence()
    text = capsys.readouterr().out
    assert "Auto-cleaned 100 finished worktree(s)." in text
    assert "finished-0" not in text
    assert len(text.splitlines()) == 1


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


def test_reap_host_owned_worktree_retires_tracking_only(tmp_path, monkeypatch):
    worktree = tmp_path / "host-worktree"
    worktree.mkdir()
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    rec = _rec("host", path=str(worktree))
    rec.checkout_managed = False
    tracking.save_record(rec, tracking_dir / "host.yaml")
    monkeypatch.setattr(
        cli.git_ops, "remove_worktree",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not remove")),
    )
    monkeypatch.setattr(
        cli.git_ops, "delete_branch",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not delete")),
    )
    monkeypatch.setattr(
        cli.procs, "terminate_processes_under",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not kill")),
    )
    monkeypatch.setattr(cli.disposition_history, "remove", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.activity, "log_event", lambda *_a, **_k: None)

    failures, warnings = cli._reap_worktree(
        rec,
        types.SimpleNamespace(),
        types.SimpleNamespace(anchor=str(tmp_path / "anchor")),
        tracking_dir,
    )

    assert failures == 0
    assert warnings == []
    assert worktree.is_dir()
    # Host-owned tracking-only retirement now archives rather than deletes
    # (archival-is-a-terminus-not-a-deletion) -- the tracking record persists
    # as a durable tombstone, distinct from the checkout it never owned.
    path = tracking_dir / "host.yaml"
    assert path.exists()
    assert tracking.load_record(path).status == "archived"


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


def test_cmd_sweep_managed_json(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cleanup_gc_cli,
        "sweep_managed_worktrees",
        lambda **_kwargs: {"removed": [{"id": "svc"}], "skipped": []},
    )
    monkeypatch.setattr(
        output,
        "_json_output",
        lambda payload: seen.setdefault("payload", payload),
    )

    assert cli.cmd_sweep_managed(argparse.Namespace(dry_run=False, json=True)) == 0
    assert seen["payload"] == {
        "removed": [{"id": "svc"}],
        "skipped": [],
    }


def test_cmd_sweep_finished_sessions_json(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli,
        "sweep_finished_session_worktrees",
        lambda **_kwargs: {"removed": [{"id": "done"}], "skipped": []},
    )
    monkeypatch.setattr(
        output,
        "_json_output",
        lambda payload: seen.setdefault("payload", payload),
    )

    assert (
        cli.cmd_sweep_finished_sessions(argparse.Namespace(dry_run=False, json=True))
        == 0
    )
    assert seen["payload"] == {
        "removed": [{"id": "done"}],
        "skipped": [],
    }
