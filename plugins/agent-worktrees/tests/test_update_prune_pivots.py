"""Tests for ``update``'s automatic stale-pivot pruning.

A Picker pivot manifest changing shape (new columns/subtitle field) on a
plugin update can leave an old materialized copy in the pivot registry
silently shadowing the correct one (see the picker-venue-pivots investigation
that produced single-line CodeSpaces rows despite the installed plugin's
manifest already carrying a ``subtitle`` field). ``update`` now auto-removes
those *legitimately stale* entries -- the same safe subset
``prunable_findings``/``prune_stale_entries`` already restrict to
(``duplicate``/``identity-mismatch``/``missing-target``/``invalid-entry``,
only once a live correct manifest for the same plugin already resolves
elsewhere) -- without requiring an operator to run
``doctor --fix --prune-pivots`` by hand. A ``legacy-unattributed`` or
``not-enabled`` finding is never auto-removed either way; those still need a
human to reinstall/re-enable the owning plugin.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import reconcile
from agent_worktrees.picker_support import pivots as pivot_registry


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _args(**over) -> argparse.Namespace:
    base = dict(
        recreate_venv=False,
        skip_modules=None,
        no_anchor_sync=True,
        no_prune_pivots=False,
        force=False,
        no_manager=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent-worktrees"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    (d / "scripts" / "install.ps1").write_text("# installer\n")
    return d


@pytest.fixture
def wired(monkeypatch, plugin_dir):
    """Wire cmd_update's collaborators to no-ops, except pivot pruning."""

    def _run(argv, *a, **k):
        return _Completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(m.subprocess, "run", _run)
    monkeypatch.setattr(m, "_registered_plugin_targets", lambda: {})
    monkeypatch.setattr(m, "_update_registered_plugins", lambda targets: None)
    monkeypatch.setattr(m, "_update_modules", lambda *a, **k: None)
    monkeypatch.setattr(m, "_fast_forward_project_anchors", lambda: None)
    monkeypatch.setattr(m, "_find_installed_plugin_dir", lambda: plugin_dir)
    monkeypatch.setattr(m, "_project_update_context", lambda: plugin_dir)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(
        reconcile,
        "runtime_deployed_version",
        lambda name, home=None, **kwargs: "1.5.3-dev9",
    )


def test_update_prunes_stale_pivots_by_default(wired, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(m, "_prune_stale_pivots_after_update", lambda: calls.append(True))

    assert m.cmd_update(_args()) == 0

    assert calls, "update must auto-prune stale pivots by default"


def test_no_prune_pivots_flag_skips_pruning(wired, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(m, "_prune_stale_pivots_after_update", lambda: calls.append(True))

    assert m.cmd_update(_args(no_prune_pivots=True)) == 0

    assert not calls, "--no-prune-pivots must skip the auto-prune"


def test_update_flags_forwards_no_prune_pivots():
    flags = m._update_flags(_args(no_prune_pivots=True))
    assert "--no-prune-pivots" in flags


def test_update_flags_omits_no_prune_pivots_by_default():
    flags = m._update_flags(_args())
    assert "--no-prune-pivots" not in flags


def test_prune_stale_pivots_after_update_applies_and_reports(monkeypatch):
    """The helper must scan without materializing, prune with apply=True, and
    report only what was actually removed -- never raising even if the
    registry helpers themselves fail (best-effort, must not fail `update`)."""
    scan_calls: list[dict] = []
    prune_calls: list[tuple] = []

    sentinel_report = object()

    def _scan(*, materialize):
        scan_calls.append({"materialize": materialize})
        return sentinel_report

    def _prune(report, *, apply):
        prune_calls.append((report, apply))
        return [
            {"entry": "agent-codespaces.abc123.json", "reason": "duplicate", "removed": True},
            {"entry": "pr-workflows.json", "reason": "not-enabled", "removed": False},
        ]

    monkeypatch.setattr(pivot_registry, "scan_pivot_registry", _scan)
    monkeypatch.setattr(pivot_registry, "prune_stale_entries", _prune)

    m._prune_stale_pivots_after_update()

    assert scan_calls == [{"materialize": False}]
    assert prune_calls == [(sentinel_report, True)]


def test_prune_stale_pivots_after_update_never_raises_on_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(pivot_registry, "scan_pivot_registry", _boom)

    m._prune_stale_pivots_after_update()  # must not raise
