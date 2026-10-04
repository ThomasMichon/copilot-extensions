"""``gc --mirror-claim-history`` wiring -- worktree-claims-transitive-finalization
Phase 3b's remote-mirroring item.

Covers the CLI plumbing only (dispatch, dry-run forwarding, graceful
degradation with no store configured, and the mirror-sync-runs-BEFORE-any-
reap ordering a reviewer round specifically flagged); ``sync_pending``'s own
git-level behavior is covered directly in ``test_claim_history_mirror.py``.
"""

from __future__ import annotations

import argparse
import types
from pathlib import Path

from agent_worktrees import __main__ as cli
from agent_worktrees import claim_history_mirror, cleanup_gc_cli


def _gc_args(**over):
    base = dict(
        dry_run=False, json=False, orphans_only=True, no_managed=True,
        no_reap_shells=True, reap_shells_grace_hours=None,
        managed_grace_hours=None, include_unused=False,
        include_conversations=False, reconcile_prs=False, max_age_days=7,
        lease_gc=False, lease_retention_days=30, lease_kind=None,
        mirror_claim_history=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _patch_gc(monkeypatch, calls: list[str]):
    from agent_worktrees import gc as gc_mod
    repo = types.SimpleNamespace(anchor="/a", remote="origin", default_branch="main")
    config = types.SimpleNamespace(default_repo=repo, repo_name="ext")
    monkeypatch.setattr(cli.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: Path("/t"))
    monkeypatch.setattr(cli.tracking, "list_records", lambda p: [])
    # cleanup_gc_cli.cmd_gc() calls cmd_cleanup() as a bare module-local
    # name (not routed through _core_helper's __main__-override check the
    # way sweep_managed_worktrees is below) -- patch it on its OWN module.
    monkeypatch.setattr(
        cleanup_gc_cli, "cmd_cleanup", lambda a: calls.append("cmd_cleanup") or 0
    )
    monkeypatch.setattr(
        cli, "sweep_managed_worktrees",
        lambda **k: calls.append("sweep_managed_worktrees") or {"removed": [], "skipped": []},
    )
    monkeypatch.setattr(cli.git_ops, "prune_worktrees", lambda **k: None)
    monkeypatch.setattr(
        gc_mod, "sweep_orphans",
        lambda *a, **k: {"scanned": False, "removed": [], "skipped": []},
    )


def test_gc_mirror_claim_history_off_by_default_never_syncs(monkeypatch):
    calls: list[str] = []
    _patch_gc(monkeypatch, calls)
    sync_calls = []
    monkeypatch.setattr(
        claim_history_mirror, "sync_pending", lambda **k: sync_calls.append(k) or {}
    )
    assert cli.cmd_gc(_gc_args()) == 0
    assert sync_calls == []


def test_gc_mirror_claim_history_forwards_dry_run(monkeypatch):
    calls: list[str] = []
    _patch_gc(monkeypatch, calls)
    sync_calls = []
    monkeypatch.setattr(
        claim_history_mirror, "sync_pending",
        lambda **k: sync_calls.append(k)
        or {"available": True, "pushed": 0, "refs": [], "failed": []},
    )
    assert cli.cmd_gc(_gc_args(mirror_claim_history=True, dry_run=True)) == 0
    assert sync_calls == [{"dry_run": True}]


def test_gc_mirror_claim_history_runs_before_the_tracked_reap(monkeypatch):
    """A reviewer round specifically flagged this ordering: the tracked
    reap (and the managed sweep) can delete tracking records a legacy
    (unstamped) claim-history event's eligibility falls back to -- syncing
    AFTER that would silently and permanently exclude those events from
    every future sweep too."""
    calls: list[str] = []
    _patch_gc(monkeypatch, calls)
    monkeypatch.setattr(
        claim_history_mirror, "sync_pending",
        lambda **k: calls.append("sync_pending") or {
            "available": True, "pushed": 0, "refs": [], "failed": [],
        },
    )
    assert cli.cmd_gc(_gc_args(
        mirror_claim_history=True, orphans_only=False, no_managed=False,
    )) == 0
    assert calls.index("sync_pending") < calls.index("cmd_cleanup")
    assert calls.index("sync_pending") < calls.index("sweep_managed_worktrees")


def test_gc_mirror_claim_history_never_fails_gc_on_an_exception(monkeypatch):
    calls: list[str] = []
    _patch_gc(monkeypatch, calls)

    def boom(**k):
        raise RuntimeError("boom")

    monkeypatch.setattr(claim_history_mirror, "sync_pending", boom)
    assert cli.cmd_gc(_gc_args(mirror_claim_history=True)) == 0
