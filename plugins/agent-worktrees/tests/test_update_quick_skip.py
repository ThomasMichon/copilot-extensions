"""Tests for the ``update`` version-gated quick-skip (dotfiles#443).

``cmd_update`` re-deploys the agent-worktrees runtime installer only when the
deployed runtime version differs from the freshly-pulled payload version (the
``devN`` version tracks commit content). When they already match it skips the
slow re-deploy -- unless ``--force`` is passed, or the deployed version cannot
be determined (no ``deploy-manifest.json``), in which case it deploys to stay
safe.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import reconcile


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _args(force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        recreate_venv=False,
        skip_modules=None,
        no_anchor_sync=True,
        force=force,
    )


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent-worktrees"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    (d / "scripts" / "install.ps1").write_text("# installer\n")
    return d


@pytest.fixture
def wired(monkeypatch, plugin_dir):
    """Wire cmd_update's collaborators to no-ops and record subprocess calls.

    Returns the list of recorded subprocess argv lists so a test can assert
    whether the runtime installer ran.
    """
    calls: list[list[str]] = []

    def _run(argv, *a, **k):
        calls.append(list(argv))
        return _Completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(m.subprocess, "run", _run)
    monkeypatch.setattr(m, "_update_registered_plugins", lambda: None)
    monkeypatch.setattr(m, "_update_modules", lambda *a, **k: None)
    monkeypatch.setattr(m, "_fast_forward_project_anchors", lambda: None)
    monkeypatch.setattr(m, "_find_installed_plugin_dir", lambda: plugin_dir)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    return calls


def _installer_ran(calls: list[list[str]]) -> bool:
    return any("install.sh" in " ".join(c) or "install.ps1" in " ".join(c)
               for c in calls)


def test_installer_skipped_when_version_current(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None: "1.5.3-dev9")
    assert m.cmd_update(_args()) == 0
    assert not _installer_ran(wired), "installer must be skipped when current"


def test_installer_runs_on_version_drift(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev10")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None: "1.5.3-dev9")
    assert m.cmd_update(_args()) == 0
    assert _installer_ran(wired), "installer must run when the version drifts"


def test_force_reruns_installer_when_current(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None: "1.5.3-dev9")
    assert m.cmd_update(_args(force=True)) == 0
    assert _installer_ran(wired), "--force must re-deploy even when current"


def test_installer_runs_when_deployed_version_unknown(wired, monkeypatch):
    # No deploy-manifest -> deployed version is None -> never skip on uncertainty.
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None: None)
    assert m.cmd_update(_args()) == 0
    assert _installer_ran(wired), "unknown deployed version must re-deploy"
