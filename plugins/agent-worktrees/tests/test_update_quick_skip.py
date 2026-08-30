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
import types
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
        no_manager=True,
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
    monkeypatch.setattr(m, "_project_update_context", lambda: plugin_dir)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    return calls


def _installer_ran(calls: list[list[str]]) -> bool:
    return any("install.sh" in " ".join(c) or "install.ps1" in " ".join(c)
               for c in calls)


def test_installer_skipped_when_version_current(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None, **kwargs: "1.5.3-dev9")
    assert m.cmd_update(_args()) == 0
    assert not _installer_ran(wired), "installer must be skipped when current"


def test_installer_runs_on_version_drift(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev10")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None, **kwargs: "1.5.3-dev9")
    assert m.cmd_update(_args()) == 0
    assert _installer_ran(wired), "installer must run when the version drifts"


def test_force_reruns_installer_when_current(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None, **kwargs: "1.5.3-dev9")
    assert m.cmd_update(_args(force=True)) == 0
    assert _installer_ran(wired), "--force must re-deploy even when current"


def test_selected_context_never_runs_self_legacy_installer(
    wired,
    monkeypatch,
    plugin_dir,
):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev10")
    monkeypatch.setattr(
        reconcile,
        "_selected_runtime_root",
        lambda name, selected_plugin_dir: (Path("/cell/agent-worktrees"), True),
    )
    monkeypatch.setattr(
        reconcile,
        "runtime_deployed_version",
        lambda name, home=None, **kwargs: "1.5.3-dev9",
    )

    assert m.cmd_update(_args(force=True)) == 0

    assert not _installer_ran(wired)


def test_prelaunch_selected_context_suppresses_self_legacy_installer(
    tmp_path,
    monkeypatch,
    plugin_dir,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    cell_root = tmp_path / "cell" / "plugins" / "agent-worktrees"
    monkeypatch.setattr(m, "_find_repo_dir", lambda: repo)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: types.SimpleNamespace(
            default_repo=types.SimpleNamespace(service_paths=None)
        ),
    )
    monkeypatch.setattr(m, "_resolve_environment", lambda config: "test")
    monkeypatch.setattr(m.svc, "discover_services", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: plugin_dir,
    )
    monkeypatch.setattr(
        reconcile,
        "_explicit_context_target",
        lambda: (Path("/context/install.json"), "agent-worktrees"),
    )
    monkeypatch.setattr(
        reconcile,
        "_selected_runtime_root",
        lambda name, selected_plugin_dir: (cell_root, True),
    )
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev10")
    monkeypatch.setattr(
        reconcile,
        "runtime_deployed_version",
        lambda name, home=None, **kwargs: "1.5.3-dev9",
    )

    plan = m.plan_pre_launch()

    assert plan["action"] == "continue"
    assert plan["diagnostics"][0]["reason"] == "context-runtime-version-drift"
    assert "updates" not in plan


def test_prelaunch_invalid_other_context_fails_closed(
    tmp_path,
    monkeypatch,
    plugin_dir,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(m, "_find_repo_dir", lambda: repo)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: types.SimpleNamespace(
            default_repo=types.SimpleNamespace(service_paths=None)
        ),
    )
    monkeypatch.setattr(m, "_resolve_environment", lambda config: "test")
    monkeypatch.setattr(m.svc, "discover_services", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: plugin_dir,
    )
    monkeypatch.setattr(
        reconcile,
        "_explicit_context_target",
        lambda: (Path("/context/install.json"), "agent-index"),
    )
    monkeypatch.setattr(
        reconcile,
        "_selected_runtime_root",
        lambda name, selected_plugin_dir: (_ for _ in ()).throw(
            ValueError("invalid cross-plugin context")
        ),
    )

    plan = m.plan_pre_launch()

    assert plan["action"] == "continue"
    assert plan["diagnostics"][0]["reason"] == "installation-context-invalid"
    assert "updates" not in plan


def test_installer_runs_when_deployed_version_unknown(wired, monkeypatch):
    # No deploy-manifest -> deployed version is None -> never skip on uncertainty.
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None, **kwargs: None)
    assert m.cmd_update(_args()) == 0
    assert _installer_ran(wired), "unknown deployed version must re-deploy"


def test_skip_still_reconciles_terminal_state_on_windows(wired, monkeypatch):
    """Even when the installer is version-skipped, a plain `update` must still
    reconcile live Windows Terminal state -- that drift is independent of our
    version, so gating it behind the skip would leave the dropdown broken
    (Test Chamber hidden / orphan cruft) forever."""
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None, **kwargs: "1.5.3-dev9")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    refreshed = {"n": 0}
    monkeypatch.setattr(m, "_refresh_terminal_profiles",
                        lambda: refreshed.__setitem__("n", refreshed["n"] + 1) or True)
    assert m.cmd_update(_args()) == 0
    assert not _installer_ran(wired), "heavy installer still skipped when current"
    assert refreshed["n"] == 1, "terminal state must be reconciled on the skip path"


def test_skip_does_not_reconcile_terminal_on_non_windows(wired, monkeypatch):
    """The skip-path terminal reconcile is Windows-only (no-op elsewhere)."""
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda name, home=None, **kwargs: "1.5.3-dev9")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    refreshed = {"n": 0}
    monkeypatch.setattr(m, "_refresh_terminal_profiles",
                        lambda: refreshed.__setitem__("n", refreshed["n"] + 1) or True)
    assert m.cmd_update(_args()) == 0
    assert refreshed["n"] == 0
