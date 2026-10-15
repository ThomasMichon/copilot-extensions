"""Profile refresh crosses the narrow Manager boundary, never an installer."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import manager_launch_cli, picker_profiles_cli as m

PLUGIN = Path(__file__).resolve().parents[1]
SUCCESS = {
    "schema": "copilot-extensions.terminal-profile-refresh",
    "version": 1,
    "status": "verified_current",
}


@pytest.fixture
def manager(monkeypatch):
    selected = SimpleNamespace(command=("manager",))
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        lambda: {"worktree-manager": selected},
    )
    monkeypatch.setattr(m.cfg, "project_name", lambda: "demo")
    return selected


def test_missing_manager_is_explicit(monkeypatch, capsys):
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests", lambda: {},
    )
    assert m._refresh_terminal_profiles() is False
    assert "Worktree Manager is unavailable" in capsys.readouterr().out


def test_refresh_uses_one_narrow_call_without_installer(manager, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr="")

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m._refresh_terminal_profiles() is True
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["manager", "refresh-profiles", "demo"]
    assert not any("install." in value or value == "--version" for value in argv)
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["timeout"] == 60


def test_explicit_registration_target_does_not_require_active_context(manager, monkeypatch):
    def no_context():
        raise AssertionError("active project lookup must not run for an explicit target")

    monkeypatch.setattr(m.cfg, "project_name", no_context)
    captured = []
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda argv, **k: captured.append(argv) or SimpleNamespace(
            returncode=0, stdout=json.dumps(SUCCESS), stderr="",
        ),
    )
    assert m._refresh_terminal_profiles("registered") is True
    assert captured == [["manager", "refresh-profiles", "registered"]]


def test_register_without_active_project_refreshes_the_registered_target(monkeypatch, tmp_path):
    from agent_worktrees import installation_cli, installer, repos

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        lambda: {"worktree-manager": SimpleNamespace(command=("manager",))},
    )
    monkeypatch.setattr(m.cfg, "_ACTIVE_PROJECT", None)
    monkeypatch.setattr(m.cfg, "project_dir", lambda name: tmp_path / "config")
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(m.cfg, "machines_yaml_path", lambda path: repo / "missing.yaml")
    monkeypatch.setattr(
        m.cfg, "load_config",
        lambda *a, **k: SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(enabled=False))),
    )
    overrides = {
        "_prepare_namespaced_project_state": lambda *a, **k: True,
        "_write_global_config": lambda *a, **k: None,
        "_write_config": lambda *a, **k: None,
        "_cleanup_stale_instructions": lambda *a, **k: None,
        "_ensure_ado_pr_cli": lambda *a, **k: None,
    }
    monkeypatch.setattr(installation_cli, "_core_helper", lambda name, local: overrides.get(name, local))
    monkeypatch.setattr(repos, "find_repo", lambda *a, **k: None)
    monkeypatch.setattr(repos, "add_repo", lambda *a, **k: None)
    monkeypatch.setattr(installer, "register_project", lambda *a, **k: None)
    monkeypatch.setattr(installer, "deploy_binstubs", lambda *a, **k: True)

    @contextmanager
    def registration(*args, **kwargs):
        yield SimpleNamespace(commit=lambda: None)

    monkeypatch.setattr(installer, "project_binstub_registration", registration)
    captured = []

    def run(argv, **kwargs):
        if argv[0] == "git":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        captured.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr="")

    monkeypatch.setattr(m.subprocess, "run", run)
    args = SimpleNamespace(
        project_name="registered", repo_dir=str(repo), machine="example-host",
        default_branch="main", force=False, agent=True,
    )
    assert installation_cli.cmd_register(args) == 0
    assert m.cfg.active_project() is None
    assert captured == [["manager", "refresh-profiles", "registered"]]


@pytest.mark.parametrize("response", [
    "old manager help text", "{}", "[]",
    json.dumps({**SUCCESS, "status": "blocked"}),
    json.dumps({**SUCCESS, "version": 2}),
])
def test_zero_exit_without_verified_protocol_is_failure(manager, monkeypatch, response):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=response, stderr=""),
    )
    assert m._refresh_terminal_profiles() is False


def test_nonzero_exit_is_failure_even_with_success_body(manager, monkeypatch):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=json.dumps(SUCCESS), stderr=""),
    )
    assert m._refresh_terminal_profiles() is False


@pytest.mark.parametrize("error", [
    OSError("cannot launch manager"), subprocess.TimeoutExpired("manager", 60),
])
def test_spawn_and_timeout_errors_are_visible(manager, monkeypatch, capsys, error):
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m._refresh_terminal_profiles() is False
    assert "Could not refresh Windows Terminal profiles" in capsys.readouterr().out


def test_real_profile_process_protocol(manager):
    manager.command = (
        sys.executable, "-c",
        "import json,sys; assert sys.argv[1:] == ['refresh-profiles','demo']; "
        f"print(json.dumps({SUCCESS!r}))",
    )
    assert m._refresh_terminal_profiles() is True


def test_installers_keep_single_registry_and_fragment_owners():
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "register-project-entry" in ps and "register-project-entry" in sh
    assert "function Write-ProjectsRegistry" not in ps
    assert "$entry['anchor']" not in ps and "entry['anchor']" not in sh
    assert "function Build-TerminalFragment" not in ps


def test_native_installer_defers_profile_side_effects_to_update():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    function = source.split("function Deploy-Shortcuts {", 1)[1].split(
        "\nfunction Deploy-CopilotPlugin {", 1,
    )[0]
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; "
         "function Deploy-TerminalFragmentViaWorktreeManager { throw 'unexpected profile deploy' }; "
         "function Deploy-Shortcuts {" + function + "\nDeploy-Shortcuts -Machine example"],
        env={**os.environ, "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": "1"},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("body", "exit_code", "expected"), [
    (json.dumps(SUCCESS), 0, True),
    (json.dumps({**SUCCESS, "status": "blocked", "error": "fixture denial"}), 1, False),
    ("old manager help", 0, False),
])
def test_native_installer_compatibility_uses_verified_manager_result(body, exit_code, expected):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    function = source.split("function Deploy-Shortcuts {", 1)[1].split(
        "\nfunction Deploy-CopilotPlugin {", 1,
    )[0]
    script = (
        "$ErrorActionPreference='Stop'; $ProjectName='demo'; "
        "function Write-ServiceWarn { param($Message) }; "
        "function Write-ServiceOk { param($Message) }; "
        "function Get-UsableWorktreeManagerBin { 'Invoke-FixtureManager' }; "
        "function Invoke-FixtureManager { "
        "if (($args -join '|') -ne 'refresh-profiles|demo|--machine|example') "
        "{ throw 'wrong Manager arguments' }; "
        f"$global:LASTEXITCODE={exit_code}; '{body.replace(chr(39), chr(39) * 2)}' "
        "}; function Deploy-Shortcuts {" + function
        + "\n$result = Deploy-Shortcuts -Machine example; "
        + f"if ($result -ne ${str(expected).lower()}) {{ throw 'wrong refresh outcome' }}"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": ""},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
