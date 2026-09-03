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
    monkeypatch.setattr(m, "_registered_plugin_targets", lambda: {})
    monkeypatch.setattr(m, "_update_registered_plugins", lambda targets: None)
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


def test_required_runtime_failure_makes_update_fail(wired, monkeypatch):
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev9")
    monkeypatch.setattr(
        reconcile,
        "runtime_deployed_version",
        lambda name, home=None, **kwargs: "1.5.3-dev9",
    )
    monkeypatch.setattr(m, "_update_modules", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        m,
        "_reconcile_registered_runtimes",
        lambda *args, **kwargs: True,
    )

    assert m.cmd_update(_args()) == 1


@pytest.mark.parametrize("status", ["ready", "deactivation-required"])
@pytest.mark.parametrize("inherited_context", [None, "/caller/install.json"])
def test_selected_context_runs_self_installer_with_validated_environment(
    wired,
    monkeypatch,
    plugin_dir,
    status,
    inherited_context,
):
    context = Path("/cell/agent-worktrees/install.json")
    cell_root = context.parent
    if inherited_context is None:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", inherited_context)
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/caller/payload")
    monkeypatch.setenv("PYTHONPATH", "/caller/python")
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.5.3-dev10")
    monkeypatch.setattr(
        reconcile,
        "resolve_runtime_installation",
        lambda name, selected_plugin_dir, **kwargs: (
            reconcile.RuntimeInstallationResolution(
                runtime_root=cell_root,
                context=context,
                actual_mode="namespaced",
                desired_mode=(
                    "namespaced" if status == "ready" else "legacy"
                ),
                status=status,
                reason=(
                    "namespaced-active"
                    if status == "ready"
                    else "policy-disabled-active"
                ),
            )
        ),
    )
    monkeypatch.setattr(
        reconcile,
        "runtime_deployed_version",
        lambda name, home=None, **kwargs: "1.5.3-dev9",
    )
    installer_environments: list[dict[str, str]] = []
    installer_argv: list[list[str]] = []

    def run(argv, *args, **kwargs):
        if "install.sh" in " ".join(argv):
            installer_environments.append(kwargs["env"])
            installer_argv.append(list(argv))
        return _Completed()

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(m.subprocess, "run", run)

    assert m.cmd_update(_args(force=True)) == 0

    assert installer_environments
    environment = installer_environments[0]
    assert environment["COPILOT_EXTENSIONS_CONTEXT"] == str(context)
    assert "COPILOT_PLUGIN_ROOT" not in environment
    assert "PYTHONPATH" not in environment
    assert installer_argv[0][-2:] == ["--install-dir", str(cell_root)]


@pytest.mark.parametrize("status", ["ready", "deactivation-required"])
@pytest.mark.parametrize("inherited_context", [None, "/caller/install.json"])
def test_prelaunch_selected_context_uses_validated_installer_environment(
    tmp_path,
    monkeypatch,
    plugin_dir,
    status,
    inherited_context,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    cell_root = tmp_path / "cell" / "plugins" / "agent-worktrees"
    cell_root.mkdir(parents=True)
    context = cell_root / "install.json"
    context.write_text("{}", encoding="utf-8")
    (cell_root / "deploy-manifest.json").write_text("{}", encoding="utf-8")
    if inherited_context is None:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", inherited_context)
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
        "resolve_runtime_installation",
        lambda name, selected_plugin_dir, **kwargs: (
            reconcile.RuntimeInstallationResolution(
                runtime_root=cell_root,
                context=context,
                actual_mode="namespaced",
                desired_mode=(
                    "namespaced" if status == "ready" else "legacy"
                ),
                status=status,
                reason=(
                    "namespaced-active"
                    if status == "ready"
                    else "policy-disabled-active"
                ),
            )
        ),
    )
    monkeypatch.setattr(m.svc, "check_staleness", lambda *args: "behind")

    plan = m.plan_pre_launch()

    assert plan["action"] == "self-update"
    update = plan["updates"][0]
    assert update["service"] == "agent-worktrees"
    assert update["runtime_root"] == str(cell_root)
    assert update["environment"] == {
        "COPILOT_EXTENSIONS_CONTEXT": str(context)
    }
    assert update["unset_environment"] == list(reconcile._RUNTIME_ENV_UNSET)
    expected_flag = "-InstallDir" if m.platform.system() == "Windows" else "--install-dir"
    assert update["argv"][-2:] == [expected_flag, str(cell_root)]


def test_prelaunch_legacy_default_uses_conventional_runtime(
    tmp_path,
    monkeypatch,
    plugin_dir,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "deploy-manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
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
        "resolve_runtime_installation",
        lambda name, selected_plugin_dir, **kwargs: (
            reconcile.RuntimeInstallationResolution(
                runtime_root=legacy_root,
                context=None,
                actual_mode="legacy",
                desired_mode="legacy",
                status="ready",
                reason="policy-default-false",
            )
        ),
    )
    monkeypatch.setattr(m.svc, "check_staleness", lambda *args: "behind")

    update = m.plan_pre_launch()["updates"][0]

    assert update["runtime_root"] == str(legacy_root)
    assert update["environment"] == {}
    assert update["unset_environment"] == list(reconcile._RUNTIME_ENV_UNSET)
    expected_flag = "-InstallDir" if m.platform.system() == "Windows" else "--install-dir"
    assert update["argv"][-2:] == [expected_flag, str(legacy_root)]


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
        "runtime_installer_environment",
        lambda name, selected_plugin_dir, **kwargs: (_ for _ in ()).throw(
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
