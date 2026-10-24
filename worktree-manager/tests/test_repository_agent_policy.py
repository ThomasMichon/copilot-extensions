"""Manager launch admission stays process-boundary and fails closed on skew."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from worktree_manager import engine_client, launcher


BIN = Path(__file__).resolve().parents[1] / "bin"


def test_engine_policy_check_is_cwd_explicit_and_project_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    result = {
        "version": 1, "repository": str(tmp_path), "expose_agent": None,
        "sources": [], "denied_by": [], "allowed": True,
    }

    def run(project: str | None, args: list[str]) -> dict:
        calls.append((project, args))
        return result

    monkeypatch.setattr(engine_client, "run_json", run)
    assert engine_client.require_agent_hosting(str(tmp_path), operation="recovery") == result
    assert calls == [(None, [
        "agent-policy", "check", "--path", str(tmp_path), "--operation", "recovery", "--json",
    ])]


@pytest.mark.parametrize("reply", [
    {}, {"version": 1, "allowed": True},
    {"version": True, "allowed": True},
    {"version": 1, "allowed": False},
    {
        "version": 1, "allowed": True, "repository": "/source",
        "sources": [], "denied_by": [], "expose_agent": False,
    },
    {
        "version": 1, "allowed": True, "repository": "relative",
        "sources": [], "denied_by": [], "expose_agent": None,
    },
    {
        "version": 1, "allowed": True, "repository": "/source",
        "sources": ["relative"], "denied_by": [], "expose_agent": None,
    },
    {
        "version": 1, "allowed": True, "repository": "/source",
        "sources": [], "denied_by": [], "expose_agent": None, "error": "denied",
    },
])
def test_incomplete_policy_never_authorizes_launch(
    monkeypatch: pytest.MonkeyPatch, reply: dict,
) -> None:
    monkeypatch.setattr(engine_client, "run_json", lambda *args: reply)
    with pytest.raises(engine_client.EngineError, match="hosting admission"):
        engine_client.require_agent_hosting("/source")


def test_old_engine_cannot_downgrade_hosting_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(project: str, args: list[str], **kwargs: object) -> dict:
        calls.append(args)
        raise engine_client.EngineError("unrecognized arguments: --require-agent-hosting")

    monkeypatch.setattr(engine_client, "run_json", run)
    with pytest.raises(engine_client.EngineFeatureUnavailable, match="hosting policy"):
        engine_client.resolve_launch_plan("demo", base=True)
    assert len(calls) == 1
    assert "--require-agent-hosting" in calls[0]


def test_launch_rechecks_after_planning_before_mux_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = engine_client.launch_plan_from_dict({
        "action": "exec", "work_dir": "/source", "cmd": ["copilot"],
        "worktree_id": "wt-a",
    })

    def denied(path: str, **kwargs: object) -> dict:
        assert path == "/source"
        raise engine_client.EngineError("repository policy changed: expose_agent: false")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("denial reached mux composition or process execution")

    monkeypatch.setattr(engine_client, "require_agent_hosting", denied)
    monkeypatch.setattr(launcher, "compose_launch", unexpected)
    monkeypatch.setattr(launcher, "execute", unexpected)
    assert launcher.launch(plan) == 1


def _function(text: str, prefix: str) -> str:
    start = text.index(prefix)
    return text[start:text.index("\n}", start) + 2]


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="native POSIX Bash required")
@pytest.mark.parametrize("allowed", [False, True])
def test_recovery_checks_actual_setup_repository_before_running_setup(
    tmp_path: Path, allowed: bool,
) -> None:
    text = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    recovery = _function(text, "run_recovery() {")
    setup = tmp_path / "tools" / "setup"
    setup.mkdir(parents=True)
    (setup / "setup.sh").write_text("printf ran > setup-ran\n", encoding="utf-8")
    engine = tmp_path / "engine"
    engine.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > policy-argv\n"
        f"exit {0 if allowed else 3}\n",
        encoding="utf-8",
    )
    engine.chmod(0o700)
    script = (
        recovery + "\nsetup_log() { :; }\ngit() { return 1; }\n"
        'admit_launch_target() { "$PYTHON" -m agent_worktrees agent-policy check --path "$1" --operation "$2" --json; }\n'
        "initialize_launch_diagnostics() { :; }\n"
        'PYTHON="$PWD/engine"\nLAUNCH_PROJECT=""\nCOPILOT_PASSTHROUGH=()\n'
        'unset COPILOT_EXTENSIONS_CONTEXT\nrun_recovery\n'
    )
    result = subprocess.run(
        [shutil.which("bash"), "-c", script], cwd=tmp_path,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == (0 if allowed else 3), result.stderr
    args = (tmp_path / "policy-argv").read_text(encoding="utf-8").splitlines()
    assert args == [
        "-m", "agent_worktrees", "agent-policy", "check",
        "--path", str(tmp_path), "--operation", "recovery", "--json",
    ]
    assert (tmp_path / "setup-ran").exists() is allowed


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("allowed", [False, True])
def test_powershell_recovery_checks_before_setup(
    tmp_path: Path, allowed: bool,
) -> None:
    text = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    recovery = _function(text, "function Invoke-RecoveryLaunch {")
    setup = tmp_path / "tools" / "setup"
    setup.mkdir(parents=True)
    (setup / "setup.ps1").write_text("Set-Content setup-ran ran\n", encoding="utf-8")
    engine = tmp_path / "engine.ps1"
    engine.write_text(
        "Set-Content policy-argv ($args -join \"`n\")\n"
        f"exit {0 if allowed else 3}\n",
        encoding="utf-8",
    )
    script = (
        recovery + "\nfunction Write-SetupLog { }\nfunction git { $global:LASTEXITCODE = 1 }\n"
        "function Assert-LaunchTarget { param($Path, $Operation); "
        "& $VenvPython -m agent_worktrees agent-policy check --path $Path --operation $Operation --json; "
        "if ($LASTEXITCODE -ne 0) { exit 3 } }\n"
        "function Initialize-LaunchDiagnostics { }\n"
        "$script:LaunchProject = ''\n$CopilotArgs = @()\n$CopilotPassthrough = @()\n"
        "$env:COPILOT_EXTENSIONS_CONTEXT = ''\n"
        "$VenvPython = Join-Path $PWD 'engine.ps1'\n"
        "Set-Alias -Name pwsh.exe -Value (Get-Command pwsh).Source\n"
        "Invoke-RecoveryLaunch\n"
    )
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-Command", script], cwd=tmp_path,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == (0 if allowed else 3), result.stderr
    args = (tmp_path / "policy-argv").read_text(encoding="utf-8").splitlines()
    assert args == [
        "-m", "agent_worktrees", "agent-policy", "check",
        "--path", str(tmp_path), "--operation", "recovery", "--json",
    ]
    assert (tmp_path / "setup-ran").exists() is allowed


def test_recovery_runtime_is_resolved_before_admission_but_setup_is_not() -> None:
    posix = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    powershell = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    assert posix.index('PYTHON="$AW_PY"') < posix.index("\n    run_recovery\n")
    assert powershell.index("$VenvPython = $AwPy") < powershell.index("\n    Invoke-RecoveryLaunch\n")
    assert "resolve --require-agent-hosting" in posix
    assert "@('resolve', '--require-agent-hosting')" in powershell
