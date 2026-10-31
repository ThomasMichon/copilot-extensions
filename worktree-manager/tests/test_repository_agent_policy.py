"""Manager launch admission stays process-boundary and fails closed on skew."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

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


def test_launch_planning_needs_no_hosting_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = []

    def run(project: str, args: list[str], **kwargs: object) -> dict:
        calls.append(args)
        if args[:2] == ["agent-policy", "check"]:
            return {
                "version": 1, "repository": str(tmp_path), "expose_agent": None,
                "sources": [], "denied_by": [], "allowed": True,
            }
        raise engine_client.EngineError("Managed agent launch refused: expose_agent: false")

    monkeypatch.setattr(engine_client, "run_json", run)
    with pytest.raises(engine_client.EngineError, match="expose_agent: false"):
        engine_client.resolve_launch_plan("demo", base=True)
    assert calls == [
        ["agent-policy", "check", "--project-root", "--json"],
        ["resolve", "--json", "--base"],
    ]


@pytest.mark.parametrize("selection", [
    {"new": True}, {"base": True}, {"worktree_id": "retained"},
])
def test_real_legacy_engine_cannot_allocate_before_manager_admission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, selection: dict,
) -> None:
    script = tmp_path / "legacy.py"
    effects = tmp_path / "effects"
    probe = tmp_path / "policy-probe"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "if 'agent-policy' in sys.argv:\n"
        f" Path({str(probe)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        " raise SystemExit(2)\n"
        f"Path({str(effects)!r}).write_text('allocated or prepared')\n"
        f"print(json.dumps({{'action':'exec','work_dir':{str(tmp_path)!r},'cmd':['copilot']}}))\n",
        encoding="utf-8",
    )
    engine_client.set_engine_command(None)
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.delenv(engine_client.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(engine_client.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(engine_client, "installed_engine_command",
                        lambda: [sys.executable, str(script)])
    with pytest.raises(engine_client.EngineError):
        engine_client.resolve_launch_plan("demo", **selection)
    assert probe.is_file()
    assert not effects.exists()


def test_compatibility_retry_rechecks_owner_before_fallback_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = []

    def request(project: str | None, args: list[str], **kwargs: object) -> dict:
        calls.append(args)
        if args[:2] == ["agent-policy", "check"]:
            if len(calls) > 1:
                raise engine_client.EngineError("receiver changed; policy unsupported")
            return {
                "version": 1, "repository": str(tmp_path), "expose_agent": None,
                "sources": [], "denied_by": [], "allowed": True,
            }
        raise engine_client.EngineError("old resolve: --json requires --worktree-id or --new")

    monkeypatch.setattr(engine_client, "run_json", request)
    with pytest.raises(engine_client.EngineError, match="receiver changed"):
        engine_client.resolve_launch_plan("demo", base=True)
    assert [args[0] for args in calls] == ["agent-policy", "resolve", "agent-policy"]


@pytest.mark.parametrize("remote", [False, True])
def test_remote_receiver_support_does_not_grant_local_namesake_admission(remote: bool) -> None:
    from worktree_manager.hosting_admission import require_owner_hosting

    denied = {
        "version": 1, "code": "repository-agent-exposure-denied",
        "error": "Managed agent launch refused: expose_agent: false",
    }
    if remote:
        assert require_owner_hosting(
            "target", None, request=lambda *_args: denied,
            failure=engine_client.EngineError, support_only=True,
        ) is None
    else:
        with pytest.raises(engine_client.EngineError, match="expose_agent: false"):
            require_owner_hosting(
                "target", None, request=lambda *_args: denied,
                failure=engine_client.EngineError,
            )


@pytest.mark.parametrize("reply", [
    {}, {"version": True, "code": "repository-agent-exposure-denied", "error": "denied"},
    {"version": 1, "error": "unknown command"},
    {"version": 1, "code": "repository-agent-exposure-denied", "error": ""},
])
def test_unsupported_engine_does_not_become_remote_launch_support(reply: dict) -> None:
    from worktree_manager.hosting_admission import require_owner_hosting

    with pytest.raises(engine_client.EngineError):
        require_owner_hosting(
            "target", None, request=lambda *_args: reply,
            failure=engine_client.EngineError, support_only=True,
        )


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
    assert 'resolve_args+=(resolve "$@")' in posix
    assert "@('resolve') + $CopilotArgs" in powershell
    assert "--require-agent-hosting" not in posix + powershell
