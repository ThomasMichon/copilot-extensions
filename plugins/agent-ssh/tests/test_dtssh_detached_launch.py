"""The dtssh host launcher starts outside the installer's lifetime.

``install-host.ps1 start``/``install``/``update`` runs from an SSH session or an
updater whose Job would take a ``Start-Process`` child down with it, so the
non-foreground start goes through WMI ``Win32_Process.Create`` and only falls
back to a contained ``Start-Process`` (with a warning) when WMI fails. The
foreground supervisor still owns and waits on its child. The functions are
extracted verbatim from the script and run under PowerShell with the
process-launch cmdlets replaced by recording functions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "transports" / "dtssh" / "scripts" / "install-host.ps1"
)
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 (pwsh) is not available")


def _function(name: str) -> str:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    start = text.index(f"function {name} {{")
    end = text.index("\nfunction ", start + 1)
    return text[start:end]


def _run(body: str, tmp_path: Path) -> list[str]:
    launcher = tmp_path / "dtssh-host-launcher.ps1"
    launcher.write_text("# launcher\n", encoding="utf-8")
    prelude = f"""
$ErrorActionPreference = 'Stop'
# The launcher is Windows-only and process creation is recorded, not real: a
# POSIX test host gets a synthetic root that its own path provider accepts.
if (-not $env:SystemRoot) {{ $env:SystemRoot = '{tmp_path}' }}
$InstallDir = '{tmp_path}'
$LauncherDst = '{launcher}'
$Alias = 'box'; $Port = 2222; $Tunnel = $null; $User = $null
$script:hostUp = $false
function Get-HostProcess {{
    if ($script:hostUp) {{ return @([pscustomobject]@{{ ProcessId = 1 }}) }}
}}
function Start-Sleep {{ param($Seconds) $script:hostUp = $true }}
function Wait-DtsshHostIdentity {{ }}
function New-CimInstance {{
    param($ClassName, [switch]$ClientOnly, $Property, $ErrorAction)
    return [pscustomobject]@{{ ClassName = $ClassName; Property = $Property }}
}}
function Start-Process {{
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
    Write-Host "start-process:$FilePath|$WorkingDirectory"
}}
"""
    script = prelude + _function("Start-DetachedHostLauncher") + "\n" + _function(
        "Start-HostLauncher"
    ) + "\n" + body
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_detached_start_goes_through_wmi_from_the_install_dir(tmp_path):
    out = _run("""
function Invoke-CimMethod {
    param($ClassName, $MethodName, $Arguments, $ErrorAction)
    $a = $Arguments
    Write-Host "cim:$ClassName.$MethodName|$($a.CurrentDirectory)|$($a.CommandLine)"
    return [pscustomobject]@{ ReturnValue = 0; ProcessId = 4321 }
}
Start-HostLauncher
""", tmp_path)

    cim = [line for line in out if line.startswith("cim:")]
    assert len(cim) == 1
    target, cwd, command = cim[0].removeprefix("cim:").split("|", 2)
    assert target == "Win32_Process.Create"
    assert cwd == str(tmp_path)
    assert "conhost.exe\" --headless \"" in command
    assert command.startswith('"') and "System32" in command
    assert "-Alias box -Port 2222" in command
    assert not any(line.startswith("start-process:") for line in out)
    assert "dtssh host started for box on port 2222" in out


def test_detached_start_passes_the_callers_environment(tmp_path):
    """A WMI child gets the provider's environment unless one is supplied, and a
    supplied block replaces it: the caller's complete environment goes through
    Win32_ProcessStartup so the launcher resolves PATH/LOCALAPPDATA as the
    installer did."""
    out = _run("""
$env:AIX_CALLER_ONLY = 'from the caller'
function Invoke-CimMethod {
    param($ClassName, $MethodName, $Arguments, $ErrorAction)
    $startup = $Arguments.ProcessStartupInformation
    Write-Host "startup:$($startup.ClassName)"
    foreach ($entry in $startup.Property.EnvironmentVariables) { Write-Host "env:$entry" }
    return [pscustomobject]@{ ReturnValue = 0; ProcessId = 4321 }
}
Start-HostLauncher
""", tmp_path)

    assert "startup:Win32_ProcessStartup" in out
    env = [line.removeprefix("env:") for line in out if line.startswith("env:")]
    assert "AIX_CALLER_ONLY=from the caller" in env
    assert any(entry.upper().startswith("PATH=") for entry in env)


@pytest.mark.parametrize("failure", ["return [pscustomobject]@{ ReturnValue = 9; ProcessId = 0 }",
                                     "throw 'wmi unavailable'"])
def test_contained_start_is_the_warned_last_resort(tmp_path, failure):
    out = _run(f"""
function Invoke-CimMethod {{
    param($ClassName, $MethodName, $Arguments, $ErrorAction)
    {failure}
}}
Start-HostLauncher 3>&1
""", tmp_path)

    assert f"start-process:conhost.exe|{tmp_path}" in out
    assert any("stops when this session ends" in line for line in out)


def test_foreground_supervisor_never_uses_wmi(tmp_path):
    out = _run("""
function Invoke-CimMethod { throw 'the foreground supervisor owns its child' }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
    Write-Host "start-process:$FilePath|$WorkingDirectory"
    $p = [pscustomobject]@{ HasExited = $false; Id = 5 }
    $p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { }
    return $p
}
Start-HostLauncher -Foreground
""", tmp_path)

    assert out[0] == f"start-process:conhost.exe|{tmp_path}"
