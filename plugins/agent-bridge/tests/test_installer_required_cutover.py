"""Execute opt-in installer cutover branches without controlling host services."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _function(shell: str, name: str) -> str:
    text = (_SCRIPTS / f"install.{shell}").read_text(encoding="utf-8")
    prefix = f"{name}() {{" if shell == "sh" else f"function {name} {{"
    start = text.index(prefix)
    return text[start:text.index("\n}\n", start) + 2]


def _shell_executable(shell: str) -> str:
    executable = shutil.which("bash" if shell == "sh" else "pwsh")
    if not executable or (shell == "sh" and os.name == "nt"):
        pytest.skip(f"{shell} is unavailable in this environment")
    return executable


def _run(tmp_path: Path, shell: str, script: str, *args: str):
    executable = _shell_executable(shell)
    path = tmp_path / f"exercise.{shell}"
    path.write_text(script, encoding="utf-8")
    prefix = [] if shell == "sh" else ["-NoProfile", "-NonInteractive", "-File"]
    return subprocess.run(
        [executable, *prefix, str(path), *args], capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("shell", ["sh", "ps1"])
@pytest.mark.parametrize("action", ["update", "install", "stop"])
def test_required_cutover_argument_is_bound_and_validated(tmp_path, shell, action):
    text = (_SCRIPTS / f"install.{shell}").read_text(encoding="utf-8")
    if shell == "sh":
        source = text[text.index('ACTION="${1:-status}"'):text.index('INSTALL_DIR="$(_normalize')]
        script = "set -eu\n" + source + '\necho "BOUND:$REQUIRE_GRACEFUL_CUTOVER"\n'
        flag = "--require-graceful-cutover"
    else:
        source = text[text.index("[CmdletBinding()]"):text.index("if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8")]
        start = text.index("if ($RequireGracefulCutover -and $Action")
        source += text[start:text.index("\n}\n", start) + 2]
        script = source + '\nWrite-Output "BOUND:$RequireGracefulCutover"\n'
        flag = "-RequireGracefulCutover"
    result = _run(tmp_path, shell, script, action, flag)
    assert (result.returncode == 0) is (action == "update"), result.stdout + result.stderr
    assert ("BOUND:" in result.stdout) is (action == "update")


def _helper_script(tmp_path: Path, shell: str, output: str, rc: int) -> str:
    _shell_executable(shell)
    if shell == "sh":
        (tmp_path / "bin").mkdir(exist_ok=True)
        (tmp_path / "bin" / "python").symlink_to(sys.executable)
        return "\n".join([
            "set -euo pipefail",
            f"VENV_DIR={shlex.quote(str(tmp_path))}",
            '_ok() { :; }; _fail() { echo "$1"; }',
            f'_bridge_cli() {{ [[ "$*" == "deploy --require-live-predecessor --json --health-timeout 180 --drain-timeout 300" ]] || return 99; printf "%s" {shlex.quote(output)}; return {rc}; }}',
            _function(shell, "_required_graceful_cutover"),
            "if _required_graceful_cutover; then echo accepted; else echo rejected; fi",
        ])
    return "\n".join([
        "Set-StrictMode -Version 2.0",
        "$ErrorActionPreference = 'Stop'; $LinkPython = 'FakePython'",
        "function Write-Ok { param($m) }; function Write-Warn { param($m) Write-Host $m }",
        "function FakePython {",
        "if ($args -contains '--force' -or $args -notcontains '--require-live-predecessor') { throw 'unsafe deploy arguments' }",
        f"$global:LASTEXITCODE = {rc}",
        "'" + output.replace("'", "''") + "'",
        "}",
        _function(shell, "Invoke-RequiredGracefulCutover"),
        "try { Invoke-RequiredGracefulCutover; 'accepted' } catch { Write-Host $_; 'rejected' }",
    ])


@pytest.mark.parametrize("unavailable", ["windows", "missing-shell"])
def test_helper_checks_shell_before_creating_files(tmp_path, monkeypatch, unavailable):
    from types import SimpleNamespace

    module = sys.modules[__name__]
    if unavailable == "windows":
        monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    else:
        monkeypatch.setattr(shutil, "which", lambda _: None)
    before = set(tmp_path.iterdir())
    with pytest.raises(pytest.skip.Exception):
        _helper_script(tmp_path, "sh", '{"ok":true}', 0)
    assert set(tmp_path.iterdir()) == before
    assert not (tmp_path / "bin").exists()


@pytest.mark.parametrize("shell", ["sh", "ps1"])
@pytest.mark.parametrize("output,rc,accepted", [
    ('{"ok":true}', 0, True),
    ('{\n"ok":true\n}', 0, True),
    ('{"ok":true}', 1, False),
    ('{"ok":false}', 0, False),
    ('{"ok":"true"}', 0, False),
    ('{"ok":1}', 0, False),
    ('{"ok":true,"skipped":true}', 0, False),
    ('{"other":true}', 0, False),
    ('[{"ok":true}]', 0, False),
    ('{"ok":false}\n{"ok":true}', 0, False),
    ("not json", 0, False),
    ("", 0, False),
])
def test_required_cutover_requires_boolean_success_and_zero_exit(tmp_path, shell, output, rc, accepted):
    result = _run(tmp_path, shell, _helper_script(tmp_path, shell, output, rc))
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("accepted" if accepted else "rejected") in result.stdout.splitlines()


def _update_script(tmp_path: Path, shell: str, strict: bool, outcome: str) -> str:
    if shell == "sh":
        return "\n".join([
            "set -euo pipefail",
            f"INSTALL_DIR={shlex.quote(str(tmp_path))}",
            'VENV_DIR="$INSTALL_DIR"',
            f"REQUIRE_GRACEFUL_CUTOVER={int(strict)}; VERSIONED_RUNTIME={0 if outcome == 'legacy' else 1}",
            "SRC_VERSION=2; _RUNTIME_CONSTRUCTION_REUSE=0; SYSTEMD_UNIT=test",
            "_step() { :; }; _ok() { :; }; _skip() { :; }; _warn() { echo \"$1\"; }; _fail() { echo \"$1\"; }",
            "_ensure_uv() { :; }; _ensure_uv_index() { :; }; _downgrade_guard() { :; }",
            "_require_install_lock() { echo locked; }; _exit_install_lock() { echo unlocked; }",
            "_payload_unchanged_since_admission() { :; }",
            _function(shell, "_reconcile_serving_runtime") if strict else "_reconcile_serving_runtime() { :; }",
            f"_guard_published_slot() {{ return {0 if outcome == 'complete' else 1}; }}",
            f"_active_is_forward() {{ return {0 if outcome == 'forward' else 1}; }}",
            "_write_update_marker() { echo marked; }; _clear_update_marker() { echo cleared; }",
            "_lifecycle_finish() { :; }; _active_signature() { echo signature; }",
            f"_get_pid() {{ return {1 if outcome == 'stopped' else 0}; }}",
            "systemctl() { return 1; }",
            f"_versioned_current() {{ echo {2 if outcome == 'same-slot' else 1}; }}",
            "_runtime_healthy() { return 1; }; _versioned_slot_clean() { echo candidate; }",
            "_update_core() { echo built; }; _versioned_mark_complete() { :; }",
            "_versioned_activate() { echo selected; }; _remove_venv_backup() { :; }",
            "deploy_binstub() { :; }; _install_systemd_unit() { :; }; _write_deploy_manifest() { echo manifest; }",
            f"_required_graceful_cutover() {{ echo strict-cutover; return {1 if outcome == 'failed' else 0}; }}",
            f"_bridge_cli() {{ echo legacy-cutover; return {1 if outcome == 'failed' else 0}; }}",
            "_update_lifecycle_drain_stop() { echo UNSAFE-STOP; }",
            "_update_lifecycle_start() { echo UNSAFE-START; }; _versioned_gc() { echo gc; }",
            _function(shell, "do_update"),
            "do_update",
        ])
    return "\n".join([
        "Set-StrictMode -Version 2.0",
        "$ErrorActionPreference = 'Stop'",
        f"$RequireGracefulCutover = ${str(strict).lower()}",
        f"$VersionedRuntime = ${str(outcome != 'legacy').lower()}",
        "$script:RuntimeConstructionReuse = $false",
        f"$InstallDir = '{tmp_path}'; $SrcVersion = '2'; $VenvPython = 'FakePython'; $LinkPython = 'FakePython'",
        "function Write-Step { param($m) }; function Write-Ok { param($m) }; function Write-Skip { param($m) }",
        "function Write-Warn { param($m) Write-Host $m }; function Write-Fail { param($m) Write-Host $m }",
        "function Ensure-Uv { return $true }; function Enter-InstallLock { Write-Host locked; return $true }",
        "function Exit-InstallLock { Write-Host unlocked }; function Test-PayloadUnchangedSinceAdmission { return $true }",
        "function Get-PayloadHash { return 'hash' }",
        f"function Test-SlotContentCurrent {{ return ${str(outcome == 'complete').lower()} }}",
        "function Get-SlotAdmission { return 'construct' }; function Invoke-VersionedSlotClean {}",
        f"function Test-ActiveIsForward {{ return ${str(outcome == 'forward').lower()} }}",
        "function Write-UpdateMarker { Write-Host marked }; function Clear-UpdateMarker { Write-Host cleared }",
        "function Get-ActiveSignature { return 'signature' }",
        f"function Get-RunningProcess {{ {'return $null' if outcome == 'stopped' else 'return 1234'} }}",
        f"function Get-VersionedCurrent {{ return '{2 if outcome == 'same-slot' else 1}' }}",
        "function Test-RuntimeHealthy { return $true }; function Backup-Venv { return $true }",
        "function Invoke-UpdateSlotBuild { Write-Host built }; function Invoke-VersionedMarkComplete { return $true }",
        "function Invoke-VersionedActivate { Write-Host selected; return $true }; function Remove-VenvBackup {}",
        "function Write-Binstubs {}; function Write-DeployManifest { Write-Host manifest }; function Ensure-ScheduledTask {}",
        "function FakePython {",
        "if ($args -contains 'deploy') { Write-Host legacy-cutover }",
        f"$global:LASTEXITCODE = {1 if outcome == 'failed' else 0}",
        "}",
        "function Invoke-RequiredGracefulCutover { Write-Host strict-cutover;",
        "throw 'Required graceful cutover was not confirmed'" if outcome == "failed" else "return",
        "}",
        _function(shell, "Invoke-ServingRuntimeReconcile") if strict else "function Invoke-ServingRuntimeReconcile {}",
        "function Invoke-UpdateDrainStop { Write-Host UNSAFE-STOP; return $true }",
        "function Invoke-UpdateStart { Write-Host UNSAFE-START }; function Invoke-VersionedGc { Write-Host gc }",
        _function(shell, "Invoke-Update"),
        "try { Invoke-Update } catch { Write-Host $_; exit 1 } finally { Exit-InstallLock }",
    ])


@pytest.mark.parametrize("shell", ["sh", "ps1"])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failed", "stopped", "forward", "same-slot", "legacy", "complete"])
def test_update_preserves_required_cutover_and_legacy_fallback(tmp_path, shell, strict, outcome):
    result = _run(tmp_path, shell, _update_script(tmp_path, shell, strict, outcome))
    output = result.stdout + result.stderr
    refused = strict and outcome in {"failed", "stopped", "forward", "same-slot", "legacy"}
    assert (result.returncode != 0) is refused, output
    if strict:
        assert "UNSAFE-" not in output, output
        if refused:
            assert "gc" not in result.stdout.splitlines(), output
            if outcome != "failed":
                assert "built" not in result.stdout.splitlines(), output
        else:
            assert "strict-cutover" in output
    elif outcome == "failed":
        assert "UNSAFE-STOP" in output and "UNSAFE-START" in output, output
    if "marked" in result.stdout.splitlines():
        assert "cleared" in output, output
