"""Closed-wheel callers can retain ownership of service startup and task policy."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from . import test_installer_engine_adoption as installer

PS5 = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = [pytest.mark.timeout(60), pytest.mark.installer_only]


@pytest.fixture(params=[
    pytest.param(
        installer.PWSH, id="pwsh",
        marks=pytest.mark.skipif(installer.PWSH is None, reason="pwsh unavailable"),
    ),
    pytest.param(
        PS5, id="powershell5",
        marks=pytest.mark.skipif(PS5 is None, reason="Windows PowerShell unavailable"),
    ),
])
def native_shell(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    shell = request.param
    assert isinstance(shell, str)
    monkeypatch.setattr(installer, "PWSH", shell)
    return shell


def _call(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    result: subprocess.CompletedProcess[str] = installer._run(
        "ps1", script, tmp_path, installer._env(tmp_path),
    )
    return result


@pytest.mark.parametrize("case", [
    "runtime-only", "default-start", "disabled-task", "disabled-config",
])
def test_owner_sync_keeps_caller_and_existing_holds(
    tmp_path: Path, native_shell: str, case: str,
) -> None:
    script = (
        installer._function("ps1", "Sync-ConnectionOwnerService")
        + f"\n$fixtureCase='{case}'\n"
        + f"$NoService=${str(case == 'runtime-only').lower()}\n"
        + r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$script:starts=0
$script:policyReads=0
$script:configReads=0
$LocalBin=$PSScriptRoot
$InstallDir=$PSScriptRoot
$LinkPython='Invoke-FixturePython'
function Write-ServiceOk {}
function Write-ServiceWarn {}
function Get-ScheduledTask {}
function Get-WindowsScheduledTaskPolicy { $script:policyReads++; 'preserve' }
function Get-OwnerScheduledTask {
    if ($fixtureCase -eq 'disabled-task') {
        return [pscustomobject]@{State='Disabled';Settings=[pscustomobject]@{Enabled=$false}}
    }
    return $null
}
function Get-ConnectionOwnerConfig {
    $script:configReads++
    [pscustomobject]@{Known=$true;Enabled=($fixtureCase -ne 'disabled-config');Interval=15}
}
function Invoke-FixturePython { $script:starts++; $global:LASTEXITCODE=0 }
Sync-ConnectionOwnerService
$expectedStarts=if ($fixtureCase -eq 'default-start') {1} else {0}
if ($script:starts -ne $expectedStarts) { throw "Unexpected Owner starts: $script:starts" }
if ($NoService -and ($script:policyReads -or $script:configReads)) {
    throw 'Runtime-only construction queried service or task policy'
}
if ($fixtureCase -eq 'disabled-task' -and $script:configReads) {
    throw 'A disabled task did not retain its startup hold'
}
"""
    )
    result = _call(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("requested", [False, True])
def test_native_driver_forwards_runtime_only_without_changing_old_callers(
    tmp_path: Path, native_shell: str, requested: bool,
) -> None:
    entry = str(installer.PLUGIN / "scripts" / "install.ps1").replace("'", "''")
    script = f"$entry='{entry}'\n$global:fixtureRequestedHold=${str(requested).lower()}\n" + r"""
$ErrorActionPreference='Stop'
$tokens=$null
$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($entry,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Installer did not parse' }
$driver=@($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Extent.Text.Contains('Enter-NativeInstallerDriver')
})
if ($driver.Count -ne 1) { throw 'Native driver boundary is ambiguous' }
# Exercise the admitted forwarding body without relaxing outer test containment.
$forward=($driver[0].Clauses[0].Item2.Statements | ForEach-Object {$_.Extent.Text}) -join "`n"
$receiver=@'
function Enter-NativeInstallerDriver {
    param($Payload,$Root,$Plugin,$Action,[string[]]$Options,
          $WheelProfile,$WheelDirectory,$WheelPython,$WheelUv)
    $script:options=@($Options)
    $script:inputs=@($WheelProfile,$WheelDirectory,$WheelPython,$WheelUv)
    [pscustomobject]@{Participant=$true;ExitCode=0;Python='fixture-python';Arguments=@()}
}
'@
$check=@'
if (('NoService' -in $script:options) -ne $global:fixtureRequestedHold) {
    throw 'Runtime-only option was not preserved at the native driver boundary'
}
if (($script:inputs -join ',') -ne 'profile,wheels,python,uv') {
    throw 'Closed-wheel inputs changed at the native driver boundary'
}
'@
$invoke=Join-Path $PSScriptRoot 'receiver.ps1'
[IO.File]::WriteAllText($invoke,$ast.ParamBlock.Extent.Text+"`n"+$receiver+"`n"+$forward+"`n"+$check)
$arguments=@{Action='update';InstallDir=$PSScriptRoot;WheelProfile='profile';
             WheelDirectory='wheels';WheelPython='python';WheelUv='uv'}
if ($global:fixtureRequestedHold) { $arguments.NoService=$true }
& $invoke @arguments
"""
    result = _call(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("arguments", [
    "update -NoService",
    "status -NoService -WheelProfile profile -WheelDirectory wheels -WheelPython python -WheelUv uv",
    "update -NoService -DryRun -WheelProfile profile -WheelDirectory wheels -WheelPython python -WheelUv uv",
])
def test_runtime_only_rejects_incomplete_or_incompatible_requests_before_effects(
    tmp_path: Path, native_shell: str, arguments: str,
) -> None:
    entry = str(installer.PLUGIN / "scripts" / "install.ps1").replace("'", "''")
    root = tmp_path / "runtime"
    quoted_root = str(root).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'\n"
        + f"& '{entry}' {arguments} -InstallDir '{quoted_root}'\n"
    )
    result = _call(script, tmp_path)
    assert result.returncode != 0
    assert "requires complete closed wheels" in result.stdout + result.stderr
    assert not root.exists()
