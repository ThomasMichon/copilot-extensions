"""The dtssh host launcher starts at logon from a per-user scheduled task.

Explorer starts Run-key and Startup-folder entries one at a time, so a Startup
shortcut can fire many minutes after a busy sign-in. A logon task fires as the
session starts; one whose trigger and principal name the current user needs no
elevation. The installer keeps exactly one logon path: the task, or -- where
policy refuses it -- the Startup shortcut. The functions are extracted verbatim
from the script and run under PowerShell with the scheduled-task cmdlets
replaced by recording functions.
"""

from __future__ import annotations

import os
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


def _pwsh(script: str) -> list[str]:
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line.strip()]


RECORDING_CMDLETS = """
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
    "action:$Execute|$Argument|$WorkingDirectory" }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User)
    "trigger:logon=$AtLogOn|user=$User" }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel)
    "principal:$UserId|$LogonType|$RunLevel" }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries,
    $ExecutionTimeLimit, $MultipleInstances)
    "settings:limit=$ExecutionTimeLimit|instances=$MultipleInstances" }
"""


def _task_name_line() -> str:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    return next(line for line in text.splitlines() if line.startswith("$LogonTaskName = "))


def _register_script(tmp_path: Path, register: str) -> str:
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"$InstallDir = '{tmp_path}'\n"
        + _task_name_line() + "\n"
        + RECORDING_CMDLETS + register + "\n"
        + _function("Register-LogonTask") + "\n" + _function("Test-LogonTaskCurrent") + "\n" + _function("Get-LogonTask")
        + "\nWrite-Host \"me:$([Security.Principal.WindowsIdentity]::GetCurrent().Name)\""
        + "\nWrite-Host \"sid:$([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)\""
        + "\nWrite-Host \"result:$(Register-LogonTask -Execute 'conhost.exe' -Argument '--headless pwsh x' 3>&1)\"\n"
    )


def _register(tmp_path: Path, register: str) -> list[str]:
    return _pwsh(_register_script(tmp_path, register))


def _value(out: list[str], key: str) -> str:
    return next(line for line in out if line.startswith(key + ":")).removeprefix(key + ":")


@pytest.mark.skipif(os.name != "nt", reason="Windows identity and scheduled tasks")
def test_the_logon_task_is_scoped_to_the_current_user(tmp_path):
    out = _register(tmp_path, """
function Get-ScheduledTask { param($TaskName, $ErrorAction) }
function Register-ScheduledTask { param($TaskName, $Action, $Trigger, $Principal, $Settings, $Description,
    [switch]$Force, $ErrorAction)
    Write-Host "task:$TaskName"; Write-Host $Action; Write-Host $Trigger; Write-Host $Principal; Write-Host $Settings }
""")
    me, sid = _value(out, "me"), _value(out, "sid")
    # Task names are machine-wide: the SID keeps another account from replacing or removing it.
    assert f"task:agent-ssh-dtssh-host-{sid}" in out
    assert f"action:conhost.exe|--headless pwsh x|{tmp_path}" in out
    # An any-user logon trigger needs elevation; one naming this user does not.
    assert f"trigger:logon=True|user={me}" in out
    assert f"principal:{me}|Interactive|Limited" in out
    assert "settings:limit=00:00:00|instances=IgnoreNew" in out
    assert out[-1] == "result:True"


REFUSED = """
function Register-ScheduledTask { param($TaskName, $Action, $Trigger, $Principal, $Settings, $Description,
    [switch]$Force, $ErrorAction)
    throw 'Access is denied.' }
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows identity and scheduled tasks")
def test_a_refused_logon_task_reports_the_startup_fallback(tmp_path):
    out = _register(tmp_path, REFUSED + "function Get-ScheduledTask { param($TaskName, $ErrorAction) }\n")
    assert "Access is denied." in out[-1] and "Startup-folder shortcut" in out[-1]
    assert out[-1].endswith(" False")


@pytest.mark.skipif(os.name != "nt", reason="Windows identity and scheduled tasks")
def test_a_refused_update_keeps_an_identical_task(tmp_path):
    """Re-registering can be refused where the task already does exactly this:
    keep it rather than fall back to the slower Startup shortcut."""
    out = _register(tmp_path, REFUSED + f"""
function Get-ScheduledTask {{ param($TaskName, $ErrorAction)
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name.Split('\\')[-1]  # Task Scheduler reports it bare
    [pscustomobject]@{{
        Actions = @([pscustomobject]@{{ Execute = 'conhost.exe'; Arguments = '--headless pwsh x'; WorkingDirectory = '{tmp_path}' }})
        Triggers = @([pscustomobject]@{{ CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskLogonTrigger' }}; UserId = $me }})
        Principal = [pscustomobject]@{{ UserId = $me; LogonType = 'Interactive' }}
    }} }}
function Unregister-ScheduledTask {{ param($TaskName, [switch]$Confirm, $ErrorAction) Write-Host "unregister:$TaskName" }}
""")
    assert not any(line.startswith("unregister:") for line in out)
    assert out[-1] == "result:True"


@pytest.mark.skipif(os.name != "nt", reason="Windows identity and scheduled tasks")
def test_a_refused_update_retires_the_old_task_before_the_fallback(tmp_path):
    """An existing task would still fire beside the shortcut with stale arguments."""
    out = _register(tmp_path, REFUSED + """
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{ TaskName = $TaskName } }
function Unregister-ScheduledTask { param($TaskName, [switch]$Confirm, $ErrorAction) Write-Host "unregister:$TaskName" }
""")
    assert f"unregister:agent-ssh-dtssh-host-{_value(out, 'sid')}" in out
    assert out[-1].endswith(" False")


@pytest.mark.skipif(os.name != "nt", reason="Windows identity and scheduled tasks")
def test_an_old_task_that_cannot_be_retired_is_a_conflict(tmp_path):
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", _register_script(tmp_path, REFUSED + """
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{ TaskName = $TaskName } }
function Unregister-ScheduledTask { param($TaskName, [switch]$Confirm, $ErrorAction) throw 'still denied' }
""")],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode != 0
    assert "or remove it (still denied)" in proc.stderr
    assert "result:" not in proc.stdout


@pytest.mark.skipif(os.name != "nt", reason="the ScheduledTasks module is Windows-only")
def test_status_and_uninstall_survive_without_the_scheduled_task_cmdlets(tmp_path):
    out = _pwsh(
        "$ErrorActionPreference = 'Stop'\n"
        "$PSModuleAutoLoadingPreference = 'None'\n"
        "Remove-Module ScheduledTasks -ErrorAction SilentlyContinue\n"
        "$LogonTaskName = 'agent-ssh-dtssh-host-S-1-5-21-0'\n"
        + _function("Get-LogonTask") + "\n" + _function("Unregister-LogonTask")
        + "\n\"task:$($null -eq (Get-LogonTask))\"\nUnregister-LogonTask\n'unregistered'\n"
    )
    assert out == ["task:True", "unregistered"]


@pytest.mark.skipif(os.name != "nt", reason="Startup-folder shortcuts are a Windows Shell feature")
@pytest.mark.parametrize("registered", [True, False], ids=["logon-task", "policy-refused"])
def test_install_keeps_exactly_one_logon_path(tmp_path, registered):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    for name in ("daemon_instances.ps1", "daemon_instance_runtime.pyz", "installer-instance-reconcile.ps1"):
        (helpers / name).write_text(f"synthetic {name}\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "dtssh-host-launcher.ps1").write_text("# launcher\n")
    (src / "install-host.ps1").write_text("# installer\n")
    install = tmp_path / "installed"
    startup = tmp_path / "agent-ssh-dtssh-host.lnk"
    if registered:
        startup.write_text("left by an older install")
    out = _pwsh(
        "$ErrorActionPreference = 'Stop'\n"
        f"$InstallDir = '{install}'\n"
        f"$LauncherSrc = '{src / 'dtssh-host-launcher.ps1'}'; $LauncherDst = '{install / 'dtssh-host-launcher.ps1'}'\n"
        f"$InstallerSrc = '{src / 'install-host.ps1'}'; $InstallerDst = '{install / 'install-host.ps1'}'\n"
        f"$StartupLnk = '{startup}'\n"
        "$Alias = 'box'; $Port = 2222; $Tunnel = $null; $User = $null; $HostKeyBackupRoot = $null\n"
        f"function Get-DaemonInstanceHelperRoot {{ '{helpers}' }}\n"
        "function Register-LogonTask { param($Execute, $Argument)\n"
        "    Write-Host \"register:$Execute|$Argument\"; return $" + ("true" if registered else "false") + " }\n"
        + _function("Install-Shortcut")
        + "\nInstall-Shortcut\n"
    )
    register = next(line for line in out if line.startswith("register:"))
    assert register.endswith(" start -Alias \"box\" -Port 2222")
    assert "conhost.exe|--headless " in register
    assert startup.exists() is (not registered)
