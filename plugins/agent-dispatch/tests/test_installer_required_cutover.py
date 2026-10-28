"""Execute both installers' opt-in cutover and no-fallback boundaries."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_install_sh_activation_guard import _BASH
from tests.test_install_sh_activation_guard import _assert_cutover_lock_released
from tests.test_install_sh_activation_guard import _extract_function_block as sh_function
from tests.test_install_sh_activation_guard import _native_bash_environment

_ROOT = Path(__file__).resolve().parents[1]
_PWSH = shutil.which("pwsh")


def ps_function(name: str) -> str:
    text = (_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start = text.index(f"function {name} {{")
    admission = (
        ps_function("Assert-DispatchActions")
        if name in {
            "Invoke-CoordinatorCutover", "Invoke-VersionedActivate", "Invoke-Update",
            "Install-RetainedRuntime", "Install-Runtime",
        }
        else ""
    )
    return admission + "\n" + text[start:text.index("\n}\n", start) + 2]


def _run(tmp_path: Path, shell: str, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    executable = _BASH if shell == "sh" else _PWSH
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    if shell == "sh":
        policy = tmp_path / "action-controls.json"
        if not policy.exists():
            policy.write_text("{}", encoding="utf-8")
        script = "\n".join([
            f"SCRIPT_DIR={shlex.quote((_ROOT / 'scripts').as_posix())}",
            f"_bootstrap_python() {{ printf '%s\\n' {shlex.quote(Path(sys.executable).as_posix())}; }}",
            '_fail() { printf \'%s\\n\' "$*" >&2; }',
            script,
        ])
    path = tmp_path / f"exercise.{shell if shell == 'sh' else 'ps1'}"
    path.write_text(script, encoding="utf-8")
    prefix = [] if shell == "sh" else ["-NoProfile", "-NonInteractive", "-File"]
    return subprocess.run(
        [executable, *prefix, str(path), *args],
        capture_output=True, text=True, timeout=30,
        env=_native_bash_environment() if shell == "sh" else None,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("shell", ["sh", "ps1"])
@pytest.mark.parametrize("action,no_service,valid", [
    ("update", False, True), ("update", True, False), ("install", False, False),
])
def test_required_cutover_argument_is_bound_and_validated(
    tmp_path: Path, shell: str, action: str, no_service: bool, valid: bool,
) -> None:
    text = (_ROOT / "scripts" / f"install.{shell}").read_text(encoding="utf-8")
    if shell == "sh":
        parse = text[text.index('ACTION="${1:-status}"'):text.index('INSTALL_DIR="${INSTALL_DIR:-')]
        script = 'set -eu\n_fail() { echo "$1"; }\n' + parse + '\necho "BOUND:$REQUIRE_GRACEFUL_CUTOVER"\n'
        args = [action, "--require-graceful-cutover", *(["--no-service"] if no_service else [])]
    else:
        parse = text[text.index("[CmdletBinding()]"):text.index("if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8")]
        validation_start = text.index("if ($RequireGracefulCutover -and ($Action")
        parse += text[validation_start:text.index("\n}\n", validation_start) + 2]
        script = parse + '\nWrite-Output "BOUND:$RequireGracefulCutover"\n'
        args = [action, "-RequireGracefulCutover", *(["-NoService"] if no_service else [])]
    result = _run(tmp_path, shell, script, *args)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert ("BOUND:" in result.stdout) is valid
    if valid:
        assert ("BOUND:1" if shell == "sh" else "BOUND:True") in result.stdout


def _update_script(tmp_path: Path, shell: str, strict: bool, outcome: str) -> str:
    if shell == "sh":
        return "\n".join([
            "set -euo pipefail",
            f'INSTALL_DIR={shlex.quote(tmp_path.as_posix())}',
            "NO_SERVICE=0",
            f"REQUIRE_GRACEFUL_CUTOVER={int(strict)}",
            'SUPERVISOR_UNIT=test; SUPERVISOR_ENV_FILE=test',
            '_step() { :; }; _ok() { :; }; _skip() { :; }; _fail() { echo "$1"; }',
            '_write_update_marker() { :; }; _clear_update_marker() { echo cleared; }',
            '_lifecycle_finish() { :; }; _downgrade_guard() { :; }',
            '_ensure_runtime() { echo selected; }; _activation_superseded_now() { return 1; }',
            f'_coordinator_cutover() {{ echo cutover; return {0 if outcome == "success" else 1}; }}',
            '_supervisor_audit() { :; }',
            '_install_service() { echo "service:${1:-restart}"; }',
            '_install_supervisor_service() { echo supervisor; }',
            sh_function("do_update"),
            "do_update",
        ])
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$InstallDir = '{tmp_path}'",
        "$NoService = $false",
        f"$RequireGracefulCutover = ${str(strict).lower()}",
        "$script:GlobalActivationLockTimeoutSeconds = 1",
        "function Write-Step { param($m) }; function Write-Ok { param($m) }",
        "function Write-Skip { param($m) }; function Write-UpdateMarker {}",
        "function Clear-UpdateMarker { Write-Host cleared }",
        "function Invoke-DowngradeGuard {}",
        "function Install-Runtime { Write-Host selected }",
        "function Test-ActivationSupersededNow { return $false }",
        "function Enter-PluginSnapshotLock { return [Threading.Mutex]::new($true) }",
        f"function Test-CoordinatorHealthy {{ return ${str(outcome != 'unhealthy').lower()} }}",
        f"function Test-CoordinatorRouted {{ return ${str(outcome != 'unrouted').lower()} }}",
        "function Invoke-CoordinatorCutover { Write-Host cutover; return "
        f"${str(outcome == 'success').lower()} }}",
        "function Stop-DispatchProcess { param($Subcommand) Write-Host retired; return 1 }",
        "function Remove-CoordinatorFirewallRule { Write-Host firewall }",
        "function Install-CoordinatorTask { param([switch]$NoStart) Write-Host \"service:$NoStart\" }",
        "function Install-SupervisorTask { Write-Host supervisor }",
        "function Confirm-CoordinatorRunning { Write-Host restart }",
        "function Invoke-SupervisorAudit { param($Event,$Reason,$Outcome) }",
        ps_function("Invoke-Update"),
        "try { Invoke-Update } catch { Write-Output $_.Exception.Message; exit 1 }",
    ])


@pytest.mark.parametrize("shell", ["sh", "ps1"])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failed", "unrouted", "unhealthy"])
def test_update_never_falls_back_or_reconciles_after_required_cutover_failure(
    tmp_path: Path, shell: str, strict: bool, outcome: str,
) -> None:
    result = _run(tmp_path, shell, _update_script(tmp_path, shell, strict, outcome))
    output = result.stdout + result.stderr
    failed = strict and outcome != "success"
    assert (result.returncode != 0) is failed, output
    assert "selected" in output
    assert "cleared" in output
    if failed:
        assert "selection may already have changed" in output
        assert not any(word in result.stdout.splitlines() for word in (
            "supervisor", "retired", "restart", "firewall",
        )), output
        assert "service:" not in result.stdout
    else:
        assert "supervisor" in result.stdout
        expected = (
            ("service:--no-restart" if shell == "sh" else "service:True")
            if outcome == "success" else
            ("service:restart" if shell == "sh" else "service:False")
        )
        assert expected in output
    if shell == "sh":
        # A failure must release the actual cross-version activation lock too.
        _assert_cutover_lock_released(tmp_path)


def test_shell_harness_rechecks_real_policy_before_maintenance(tmp_path: Path) -> None:
    script = _update_script(tmp_path, "sh", strict=False, outcome="success")
    allowed = _run(tmp_path, "sh", script)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "selected" in allowed.stdout.splitlines()
    policy = tmp_path / "action-controls.json"
    assert json.loads(policy.read_text(encoding="utf-8")) == {}
    policy.write_text(json.dumps({
        "runtime-activation": {"disabled": True},
    }), encoding="utf-8")
    denied = _run(tmp_path, "sh", script)
    assert denied.returncode == 3, denied.stdout + denied.stderr
    assert "runtime-activation" in denied.stdout + denied.stderr
    assert not any(line in {"selected", "supervisor"} or line.startswith("service:")
                   for line in denied.stdout.splitlines())
    policy.write_text("{}", encoding="utf-8")
    cleared = _run(tmp_path, "sh", script)
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert "selected" in cleared.stdout.splitlines()
    assert "supervisor" in cleared.stdout.splitlines()


def _fake_python(
    tmp_path: Path, output: str, rc: int, routed: bool, *, shell: str,
) -> Path:
    implementation = tmp_path / "fake_python.py"
    implementation.write_text(
        "import sys\n"
        "if sys.argv[1:3] == ['-m', 'agent_dispatch']:\n"
        "    assert sys.argv[3:] == ['_cutover', '--json']\n"
        f"    print({output!r})\n    sys.exit({rc})\n"
        "if sys.argv[1] == '-c':\n"
        "    if 'read_active_endpoint' in sys.argv[2]:\n"
        f"        sys.exit({0 if routed else 1})\n"
        "    exec(sys.argv[2])\n",
        encoding="utf-8",
    )
    native_cmd = os.name == "nt" and shell == "ps1"
    path = tmp_path / ("python.cmd" if native_cmd else "python")
    wrapper = (
        f'@echo off\r\n"{sys.executable}" "{implementation}" %*\r\nexit /b %errorlevel%\r\n'
        if native_cmd else
        f'#!/bin/sh\nexec {shlex.quote(Path(sys.executable).as_posix())} '
        f'{shlex.quote(implementation.as_posix())} "$@"\n'
    )
    path.write_text(wrapper, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("shell", ["sh", "ps1"])
@pytest.mark.parametrize("output,rc,accepted", [
    ('{"ok":true}', 0, True),
    ('{\n  "ok": true\n}', 0, True),
    ('{"ok":false}', 0, False),
    ('{"ok":"true"}', 0, False),
    ('{"ok":1}', 0, False),
    ('{"ok":true}', 1, False),
    ("", 0, False),
    ("not json", 0, False),
    ('{"other":true}', 0, False),
    ('{"ok":false}\n{"ok":true}', 0, False),
    ('[{"ok":true}]', 0, False),
    ('[{"ok":true},{"ok":true}]', 0, False),
])
def test_required_cutover_requires_explicit_boolean_success_and_zero_exit(
    tmp_path: Path, shell: str, output: str, rc: int, accepted: bool,
) -> None:
    python = _fake_python(tmp_path, output, rc, routed=True, shell=shell)
    if shell == "sh":
        script = "\n".join([
            "set -euo pipefail",
            f"INSTALL_DIR={shlex.quote(tmp_path.as_posix())}",
            "NO_SERVICE=0; REQUIRE_GRACEFUL_CUTOVER=1",
            f'VENV_PYTHON={shlex.quote(python.as_posix())}; LINK_PYTHON="$VENV_PYTHON"',
            '_step() { :; }; _ok() { :; }; _warn() { echo "$1" >&2; }',
            # Only the availability check is stubbed, never a real systemd call.
            'systemctl() { return 0; }',
            sh_function("_coordinator_cutover"),
            "if _coordinator_cutover; then echo accepted; else echo rejected; fi",
        ])
    else:
        script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$InstallDir = '{tmp_path}'",
            "$RequireGracefulCutover = $true",
            f"$VenvPython = '{python}'; $LinkPython = $VenvPython",
            "function Write-Step { param($m) }; function Write-Ok { param($m) }",
            "function Write-Warn { param($m) Write-Host $m }",
            "function Invoke-SupervisorAudit { param($Event,$Reason,$Outcome) }",
            ps_function("Invoke-CoordinatorCutover"),
            "if (Invoke-CoordinatorCutover) { 'accepted' } else { 'rejected' }",
        ])
    result = _run(tmp_path, shell, script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("accepted" if accepted else "rejected") in result.stdout.splitlines()
    assert "falling back" not in result.stdout + result.stderr


def test_posix_required_cutover_rejects_missing_route_without_start(tmp_path: Path) -> None:
    python = _fake_python(tmp_path, json.dumps({"ok": True}), 0, routed=False, shell="sh")
    script = "\n".join([
        "set -euo pipefail",
        f"INSTALL_DIR={shlex.quote(tmp_path.as_posix())}",
        "NO_SERVICE=0; REQUIRE_GRACEFUL_CUTOVER=1",
        f'VENV_PYTHON={shlex.quote(python.as_posix())}; LINK_PYTHON="$VENV_PYTHON"',
        'systemctl() { return 0; }',
        sh_function("_coordinator_cutover"),
        "if _coordinator_cutover; then exit 99; fi",
    ])
    result = _run(tmp_path, "sh", script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_powershell_required_cutover_rejects_real_legacy_venv_before_stop(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$RequireGracefulCutover = $true; $VersionedRuntime = $true; $Force = $false",
        f"$InstallDir = '{tmp_path}'; $SrcVersion = '1.0.0'",
        "$script:GlobalActivationLockTimeoutSeconds = 1",
        "function Assert-SourceRuntime {}",
        "function Get-VersionedCurrent { return '1.0.0' }",
        "function Test-VenvIsLink { param($path) return $false }",
        "function Test-VersionLt { param($A,$B) return $false }",
        "function Enter-PluginSnapshotLock { return [Threading.Mutex]::new($true) }",
        "function Write-Step { param($m) Write-Host $m }",
        "function Stop-DispatchProcess { param($Subcommand) Write-Host 'UNSAFE-STOP' }",
        "function Retire-SupervisorProcesses { Write-Host 'UNSAFE-RETIRE' }",
        ps_function("Invoke-VersionedActivate"),
        "try { Invoke-VersionedActivate; exit 99 } catch { Write-Host $_.Exception.Message; exit 1 }",
    ])
    result = _run(tmp_path, "ps1", script)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no process stop attempted" in result.stdout
    assert "UNSAFE-" not in result.stdout
