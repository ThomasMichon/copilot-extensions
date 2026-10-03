"""``gc --lease-gc`` wiring -- worktree-claims-transitive-finalization Phase 5.

Covers the CLI plumbing only (dispatch, dry-run/kind/retention forwarding,
graceful degradation with no lease store configured); ``squash_stale``'s
own git-level behavior is covered directly in ``test_lease_store.py``.
"""

from __future__ import annotations

import argparse
import types
from pathlib import Path

from agent_worktrees import __main__ as cli
from agent_worktrees import lease_config
from agent_worktrees import lease_store as lease_store_mod


def _gc_args(**over):
    base = dict(
        dry_run=False, json=False, orphans_only=True, no_managed=True,
        no_reap_shells=True, reap_shells_grace_hours=None,
        managed_grace_hours=None, include_unused=False,
        include_conversations=False, reconcile_prs=False, max_age_days=7,
        lease_gc=False, lease_retention_days=30, lease_kind=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _patch_gc(monkeypatch):
    from agent_worktrees import gc as gc_mod
    repo = types.SimpleNamespace(anchor="/a", remote="origin", default_branch="main")
    config = types.SimpleNamespace(default_repo=repo, repo_name="ext")
    monkeypatch.setattr(cli.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: Path("/t"))
    monkeypatch.setattr(cli.tracking, "list_records", lambda p: [])
    monkeypatch.setattr(cli, "cmd_cleanup", lambda a: 0)
    monkeypatch.setattr(cli, "sweep_managed_worktrees",
                        lambda **k: {"removed": [], "skipped": []})
    monkeypatch.setattr(cli.git_ops, "prune_worktrees", lambda **k: None)
    monkeypatch.setattr(gc_mod, "sweep_orphans",
                        lambda *a, **k: {"scanned": False, "removed": [], "skipped": []})


def test_gc_lease_gc_off_by_default_never_touches_lease_config(monkeypatch):
    _patch_gc(monkeypatch)
    called = []
    monkeypatch.setattr(
        lease_config, "load_lease_settings",
        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError()),
    )
    assert cli.cmd_gc(_gc_args()) == 0
    assert called == []


def test_gc_lease_gc_dispatches_squash_stale_with_forwarded_options(monkeypatch):
    _patch_gc(monkeypatch)
    calls: list[dict] = []

    class _FakeStore:
        def __init__(self, settings):
            calls.append({"settings": settings})

        def squash_stale(self, **kwargs):
            calls.append(kwargs)
            return [{"ref": "refs/agent-worktrees/leases/v1/codespace/x",
                      "old_oid": "a" * 40, "new_oid": "b" * 40}]

    sentinel_settings = object()
    monkeypatch.setattr(lease_config, "load_lease_settings", lambda: sentinel_settings)
    monkeypatch.setattr(lease_store_mod, "GitLeaseStore", _FakeStore)

    assert cli.cmd_gc(_gc_args(
        lease_gc=True, dry_run=True, lease_retention_days=45, lease_kind="codespace",
    )) == 0

    assert calls[0] == {"settings": sentinel_settings}
    assert calls[1] == {"retention_days": 45, "kind": "codespace", "dry_run": True}


def test_gc_lease_gc_degrades_gracefully_with_no_lease_store_configured(monkeypatch):
    _patch_gc(monkeypatch)

    def _raise(*a, **k):
        raise lease_config.ConfigError("no state root bound")

    monkeypatch.setattr(lease_config, "load_lease_settings", _raise)

    assert cli.cmd_gc(_gc_args(lease_gc=True)) == 0


def test_gc_lease_gc_json_reports_the_sweep(monkeypatch, capsys):
    _patch_gc(monkeypatch)

    class _FakeStore:
        def __init__(self, settings):
            pass

        def squash_stale(self, **kwargs):
            return []

    monkeypatch.setattr(lease_config, "load_lease_settings", lambda: object())
    monkeypatch.setattr(lease_store_mod, "GitLeaseStore", _FakeStore)

    assert cli.cmd_gc(_gc_args(lease_gc=True, json=True)) == 0
    out = capsys.readouterr().out
    assert '"lease_gc"' in out
    assert '"available": true' in out


def test_gc_parser_has_lease_gc_flags():
    args = cli.build_parser().parse_args(
        ["gc", "--lease-gc", "--lease-retention-days", "45", "--lease-kind", "task"])
    assert args.lease_gc is True
    assert args.lease_retention_days == 45
    assert args.lease_kind == "task"
