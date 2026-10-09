"""Fail-closed boundaries for temporary PowerShell installer payloads."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def isolate_installer_environment(
    env: dict[str, str], home: Path, fake_bin: Path, pwsh: str,
) -> None:
    for key in (
        "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
        "AGENT_CODESPACES_NO_SELFPROVISION", "COPILOT_PLUGIN_INSTALL_SMOKE",
        "COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD", "COPILOT_EXTENSIONS_CONTEXT",
    ):
        env.pop(key, None)
    for key, suffix in {
        "HOME": "", "USERPROFILE": "", "AGENT_HOME": "",
        "APPDATA": "roaming", "LOCALAPPDATA": "local", "PROGRAMDATA": "program",
        "COPILOT_HOME": "copilot", "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state", "XDG_RUNTIME_DIR": "run",
        "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
    }.items():
        root = home / suffix
        root.mkdir(parents=True, exist_ok=True)
        env[key] = str(root)
    # A .cmd fake must not compete with native executables or host launchers.
    env["PATH"] = str(fake_bin)
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    env["TEST_INSTALLER_EFFECTS"] = str(fake_bin / "forbidden-effects")
    env["TEST_EXPECTED_UV"] = str(fake_bin / ("uv.cmd" if os.name == "nt" else "uv"))
    env["OS"] = "Windows_Test"
    if os.name == "nt":
        env["PATHEXT"] = ".CMD;.EXE"
        (fake_bin / "pwsh.cmd").write_text(
            f'@"{pwsh}" %*\n@exit /b %ERRORLEVEL%\n', encoding="utf-8",
        )
    else:
        for name in ("bash", "dirname", "uname", "grep", "sed", "cat", "readlink", "sort", "head", "tr"):
            command = shutil.which(name)
            assert command, f"missing fixture prerequisite: {name}"
            (fake_bin / name).symlink_to(command)
        (fake_bin / "pwsh").symlink_to(pwsh)


def guard_installer_payload(payload: Path) -> None:
    """Guard copied installers, including their self-stage/first-use children."""
    installer = payload / "scripts" / "install.ps1"
    text = installer.read_text(encoding="utf-8")
    marker = "Set-StrictMode -Version Latest"
    assert text.count(marker) == 1
    guard = r"""
function Deny-TestInstallerEffect {
    param([string]$Effect)
    [IO.File]::AppendAllText($env:TEST_INSTALLER_EFFECTS, "$Effect`n")
    throw "Forbidden installer test effect: $Effect"
}
function New-Object {
    param([string]$TypeName, [object[]]$ArgumentList)
    if ($TypeName -eq 'Net.WebClient') { Deny-TestInstallerEffect 'uv bootstrap' }
    Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName -ArgumentList $ArgumentList
}
function Get-ScheduledTask { param($TaskName, $ErrorAction) }
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) }
function Register-ScheduledTask { Deny-TestInstallerEffect 'register scheduled task' }
function Start-ScheduledTask { Deny-TestInstallerEffect 'start scheduled task' }
function Stop-ScheduledTask { Deny-TestInstallerEffect 'stop scheduled task' }
function Unregister-ScheduledTask { Deny-TestInstallerEffect 'unregister scheduled task' }
function New-ScheduledTaskAction { Deny-TestInstallerEffect 'create scheduled task' }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, [switch]$PassThru, [switch]$NoNewWindow)
    if ($FilePath -ne (Get-Process -Id $PID).Path -or
        -not ($ArgumentList -contains '-File') -or
        -not ($ArgumentList -match 'install\.ps1$')) {
        Deny-TestInstallerEffect 'start host process'
    }
    Microsoft.PowerShell.Management\Start-Process @PSBoundParameters
}
$candidate = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if (-not $candidate -or $candidate.Source -ne $env:TEST_EXPECTED_UV) {
    throw "Fixture uv not selected: $($candidate | Out-String)"
}
"""
    installer.write_text(text.replace(marker, marker + "\n" + guard), encoding="utf-8")


def assert_installer_isolated(fake_bin: Path) -> None:
    effects = fake_bin / "forbidden-effects"
    assert not effects.exists(), effects.read_text() if effects.exists() else ""
    assert not list(fake_bin.parent.rglob("tool/uv.exe")), "real uv was acquired"
