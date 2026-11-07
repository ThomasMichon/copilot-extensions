"""Regression guards for Windows S4U scheduled-task recovery (#876)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"


def _text() -> str:
    return _INSTALL_PS1.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    text = _text()
    start = text.index(f"function {name} ")
    end = text.find("\nfunction ", start + 1)
    return text[start:] if end == -1 else text[start:end]


@pytest.mark.skipif(os.name != "nt", reason="isolated Windows PowerShell installer functions")
@pytest.mark.parametrize(
    "policy,task,expected",
    [
        ("preserve", "missing", "unchanged"),
        ("preserve", "custom", "unchanged"),
        ("preserve", "disabled", "unchanged"),
        ("enabled", "missing", "registered"),
        ("enabled", "owned", "unchanged"),
        ("enabled", "disabled", "unchanged"),
        ("absent", "missing", "unchanged"),
        ("absent", "owned", "removed"),
        ("absent", "direct", "removed"),
        ("absent", "absolute-shell", "removed"),
        ("absent", "foreign-first", "refused"),
        ("absent", "owned-forwarded", "refused"),
        ("absent", "foreign-wrapper", "refused"),
        ("absent", "foreign-executor", "refused"),
        ("absent", "command", "refused"),
        ("absent", "unquoted-space", "refused"),
        ("absent", "custom", "refused"),
        ("absent", "suffix", "refused"),
        ("absent", "ambiguous", "refused"),
        ("absent", "disabled", "refused"),
        ("absent", "remove-fails", "refused"),
    ],
)
def test_scheduling_policy_is_task_only_and_repeatable(tmp_path, policy, task, expected):
    root = tmp_path / "bridge space"
    root.mkdir()
    state = root / "state"
    state.write_text("live-daemon-and-state")
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = tmp_path / "scheduling.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir={quote(root)}; $TaskName='fixture-only'; $script:kind={quote(task)}\n"
        "$script:registered=0; $script:removed=0; $script:task=$null\n"
        "function Write-Ok { param($m) }; function Write-Skip { param($m) }\n"
        f"function Get-WindowsScheduledTaskPolicy {{ {quote(policy)} }}\n"
        "function Register-ScheduledTask_ { $script:registered++; "
        "$script:task=[pscustomobject]@{State='Ready'; Principal=[pscustomobject]@{LogonType='Interactive'}} }\n"
        "function Stop-ScheduledTask { throw 'service stop forbidden' }\n"
        "function Stop-DaemonProcesses { throw 'daemon stop forbidden' }\n"
        "function Start-ScheduledTask { throw 'task start forbidden' }\n"
        "function Invoke-Start { throw 'direct ensure forbidden' }\n"
        "function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction)\n"
        " if ($script:kind -eq 'remove-fails') { throw 'access denied' }; "
        "$script:removed++; $script:task=$null }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) $script:task }\n"
        "if ($script:kind -ne 'missing') {\n"
        " $launcher=Join-Path $InstallDir 'start-agent-bridge.ps1'\n"
        " if ($script:kind -eq 'custom') { $launcher='C:\\foreign\\service.ps1' }\n"
        " if ($script:kind -eq 'suffix') { $launcher += '.foreign' }\n"
        " $actions=@([pscustomobject]@{Execute='conhost.exe'; Arguments=\"--headless powershell.exe -File `\"$launcher`\"\"})\n"
        " if ($script:kind -eq 'direct') { $actions[0].Execute='C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'; $actions[0].Arguments=\"-NoProfile -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'absolute-shell') { $actions[0].Arguments=\"--headless `\"C:\\Program Files\\PowerShell\\7\\pwsh.exe`\" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'foreign-first') { $actions[0].Arguments=\"--headless powershell.exe -File C:\\foreign.ps1 -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'owned-forwarded') { $actions[0].Arguments=\"--headless powershell.exe -File C:\\foreign.ps1 `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'foreign-wrapper') { $actions[0].Arguments=\"--headless C:\\foreign.exe -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'foreign-executor') { $actions[0].Execute='cmd.exe' }\n"
        " if ($script:kind -eq 'command') { $actions[0].Arguments=\"--headless powershell.exe -Command C:\\foreign.ps1 -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'unquoted-space') { $actions[0].Arguments=\"--headless powershell.exe -File $launcher\" }\n"
        " if ($script:kind -eq 'ambiguous') { $actions += $actions[0] }\n"
        " $script:task=[pscustomobject]@{Actions=$actions; State=$(if ($script:kind -eq 'disabled') {'Disabled'} else {'Running'}); Principal=[pscustomobject]@{LogonType='S4U'}}\n"
        "}\n"
        + _function_body("Assert-OwnedBridgeTask")
        + _function_body("Get-SchedulingPolicyTask")
        + _function_body("Ensure-ScheduledTask")
        + "\n$result='unchanged'; try { Ensure-ScheduledTask; Ensure-ScheduledTask } "
        "catch { $result='refused' }\n"
        "if ($script:registered) { $result='registered' }; if ($script:removed) { $result='removed' }\n"
        "[Console]::WriteLine((@{result=$result; removed=$script:removed; registered=$script:registered} | ConvertTo-Json -Compress))\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual["result"] == expected
    assert actual["removed"] == (1 if expected == "removed" else 0)
    assert actual["registered"] == (1 if expected == "registered" else 0)
    assert state.read_text() == "live-daemon-and-state"


@pytest.mark.skipif(os.name != "nt", reason="isolated Windows task-query outcomes")
@pytest.mark.parametrize(
    "policy,query,succeeds",
    [
        ("absent", "missing", True),
        ("absent", "denied", False),
        ("absent", "cim-failure", False),
        ("absent", "other-not-found", False),
        ("absent", "module-unavailable", False),
        ("preserve", "module-unavailable", True),
        ("preserve", "missing", True),
    ],
)
def test_scheduling_policy_distinguishes_missing_from_unknown(tmp_path, policy, query, succeeds):
    script = tmp_path / "query.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; $TaskName='fixture-only'; $script:reportedAbsent=$false\n"
        f"$query='{query}'\n"
        f"function Get-WindowsScheduledTaskPolicy {{ '{policy}' }}\n"
        "function Get-Command { if ($query -ne 'module-unavailable') { 'fixture-command' } }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction)\n"
        " if ($ErrorAction -ne 'Stop') { throw 'query must be terminating' }\n"
        " $id=if ($query -eq 'missing') {'CmdletizationQuery_NotFound_TaskName'} else {$query}\n"
        " throw [Management.Automation.ErrorRecord]::new([Exception]::new($query),"
        "$id,[Management.Automation.ErrorCategory]::ObjectNotFound,$TaskName)\n"
        "}\n"
        "function Write-Ok { param($m) $script:reportedAbsent=$true }\n"
        "function Write-Skip { param($m) }\n"
        "function Unregister-ScheduledTask { throw 'unexpected removal' }\n"
        + _function_body("Get-SchedulingPolicyTask")
        + _function_body("Ensure-ScheduledTask")
        + "\n$succeeded=$false; try { Ensure-ScheduledTask; $succeeded=$true } catch {}\n"
        "[Console]::WriteLine((@{success=$succeeded; absent=$script:reportedAbsent} | ConvertTo-Json -Compress))\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual["success"] is succeeds
    assert actual["absent"] is (succeeds and policy == "absent")


@pytest.mark.skipif(os.name != "nt", reason="native configuration reader in PowerShell")
@pytest.mark.parametrize("policy", ["preserve", "enabled", "absent", "invalid"])
def test_scheduling_policy_reads_native_retained_config(tmp_path, policy):
    if policy != "preserve":
        (tmp_path / "config.yaml").write_text(f"windows_scheduled_task: {policy}\n")
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = tmp_path / "reader.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={quote(tmp_path)}; $LinkPython={quote(sys.executable)}; $NonInteractive=$false\n"
        "$env:AGENT_BRIDGE_NONINTERACTIVE=''\n"
        + _function_body("Get-WindowsScheduledTaskPolicy")
        + "\nGet-WindowsScheduledTaskPolicy\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if policy == "invalid":
        assert result.returncode != 0
        assert "windows_scheduled_task must be" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == policy


@pytest.mark.skipif(os.name != "nt", reason="isolated Windows installer fast paths")
@pytest.mark.parametrize("action", ["Install", "Update", "Provision"])
def test_native_reconcile_fast_paths_apply_scheduling_policy(tmp_path, action):
    script = tmp_path / "fast-path.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; $script:calls=0; $ForceTaskRepair=$false\n"
        f"$LinkPython='{sys.executable}'; $SrcVersion='fixture'; $RequireGracefulCutover=$false\n"
        "function Write-Step { param($m) }; function Write-Ok { param($m) }\n"
        "function Ensure-Uv { $true }; function Invoke-MigrationCheck { }\n"
        "function Get-PayloadHash { 'fixture' }; function Test-SlotContentCurrent { $true }\n"
        "function Ensure-RuntimeSlotRetention { }; function Write-DeployManifest { }\n"
        "function Enter-InstallLock { $true }; function Exit-InstallLock { }\n"
        "function Test-PayloadUnchangedSinceAdmission { param([switch]$SourceIdentityOnly) }\n"
        "function Invoke-ServingRuntimeReconcile { }\n"
        "function Get-WindowsScheduledTaskPolicy { 'preserve' }\n"
        "function Ensure-ScheduledTask { $script:calls++ }\n"
        + _function_body(f"Invoke-{action}")
        + f"\nInvoke-{action}; Invoke-{action}; [Console]::WriteLine($script:calls)\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "2"


@pytest.mark.skipif(os.name != "nt", reason="native install-lock ownership and repair handoff")
@pytest.mark.parametrize("scenario", ["preserve", "enabled", "disabled", "query-fails", "repair", "repair-absent"])
def test_provision_serializes_policy_and_hands_repair_back_to_lock_owner(tmp_path, scenario):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "resolve-runtime.ps1").write_text(
        f"Assert-Locked; $AgentRtPy='{sys.executable}'\n", encoding="utf-8-sig",
    )
    body = _function_body("Invoke-Provision")
    start = body.index("        $isAdmin = (")
    end = body.index("\n        if (-not $isAdmin", start)
    body = body[:start] + "        $isAdmin = $global:elevated" + body[end:]
    repair_source = (_PLUGIN_ROOT / "scripts" / "repair-scheduled-task.ps1").read_text(encoding="utf-8")
    repair_tail = repair_source[repair_source.index("$installer = Join-Path $PSScriptRoot 'install.ps1'"):]
    (tmp_path / "repair.ps1").write_text(repair_tail, encoding="utf-8-sig")
    (tmp_path / "install.ps1").write_text(
        "param($Action,$InstallDir,[switch]$ForceTaskRepair)\n"
        "if ($Action -ne 'provision') { throw 'unexpected repair action' }\n"
        "$LinkPython=Join-Path $InstallDir 'unbuilt-incoming\\python.exe'\n"
        "Invoke-Provision\n$global:LASTEXITCODE=0\n", encoding="utf-8-sig",
    )
    script = tmp_path / "lock-owner.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir='{tmp_path}'; $LinkPython='{sys.executable}'; $TaskName='fixture'\n"
        f"$global:scenario='{scenario}'; $global:elevated=$false; $global:policy='enabled'\n"
        "if ($global:scenario -eq 'preserve') { $global:policy='preserve' }\n"
        "$global:registered=0; $global:repaired=0; $global:ensured=0; $NonInteractive=$false; $ForceTaskRepair=$false\n"
        "$env:AGENT_BRIDGE_NONINTERACTIVE=''; $ScheduledTaskHasNotRunResult=267011\n"
        "function Write-Step { param($m) }; function Write-Skip { param($m) }\n"
        "function Write-Fail { param($m) throw $m }\n"
        "function Get-PayloadHash { 'fixture' }; function Ensure-RuntimeSlotRetention {"
        " if ($ForceTaskRepair) { throw 'task repair must not touch runtime retention' } }\n"
        "function Assert-Locked {\n"
        " if (-not $script:InstallLockHandle) { throw 'unlocked policy or mutation' }\n"
        " $other=$null; try { $other=[IO.File]::Open((Join-Path $InstallDir '.install.lock'),'OpenOrCreate','ReadWrite','None') } catch [IO.IOException] {}\n"
        " if ($other) { $other.Dispose(); throw 'lock does not exclude another owner' }\n"
        "}\n"
        "function Get-WindowsScheduledTaskPolicy { Assert-Locked; $global:policy }\n"
        "function Ensure-ScheduledTask { Assert-Locked; $global:ensured++ }\n"
        "function Get-SchedulingPolicyTask { param($Policy) Assert-Locked\n"
        " if ($global:scenario -eq 'query-fails') { throw 'CIM unavailable' }\n"
        " [pscustomobject]@{State=$(if ($global:scenario -eq 'disabled') {'Disabled'} else {'Ready'});"
        " Principal=[pscustomobject]@{LogonType=$(if ($global:scenario -like 'repair*') {'S4U'} else {'Interactive'})}} }\n"
        "function Assert-OwnedBridgeTask { param($Task) Assert-Locked }\n"
        "function Get-ScheduledTaskLastResult { param($Name) Assert-Locked; 267011 }\n"
        "function Register-ScheduledTask_ { param([switch]$ForceTaskRepair) Assert-Locked;"
        " if ($global:scenario -eq 'repair' -and -not $ForceTaskRepair) { throw 'repair must refresh settings' }; $global:registered++ }\n"
        f"function Get-ScheduledTaskRepairScript {{ '{tmp_path / 'repair.ps1'}' }}\n"
        "function Get-PwshPath { 'Invoke-FixtureRepair' }\n"
        "function Invoke-FixtureRepair { param([switch]$NoProfile,$ExecutionPolicy,[switch]$NonInteractive,$File,$InstallDir,$TaskName)\n"
        " if ($TaskName -ne 'fixture') { throw 'repair lost selected task name' }\n"
        f" if ($InstallDir -ne '{tmp_path}') {{ throw 'repair lost selected installation root' }}\n"
        " if ($script:InstallLockHandle) { throw 'repair child inherits held lock' }\n"
        " $global:repaired++; if ($global:repaired -gt 1) { throw 'recursive repair' }\n"
        " $global:elevated=$true\n"
        " if ($global:scenario -eq 'repair-absent') { $global:policy='absent' }\n"
        " & $File; $global:LASTEXITCODE=0\n"
        "}\n"
        "function Start-ScheduledTask { throw 'task start forbidden' }\n"
        "function Invoke-Start { throw 'direct ensure forbidden' }\n"
        "function Invoke-Install { throw 'task repair must not build incoming runtime' }\n"
        + _text()[_text().index("$script:InstallLockHandle = $null"):_text().index("function Invoke-VersionedGc")]
        + body
        + "\n$failed=$false; try { Invoke-Provision } catch { if ($_.Exception.Message -ne 'CIM unavailable') { throw }; $failed=$true }\n"
        "$probe=[IO.File]::Open((Join-Path $InstallDir '.install.lock'),'OpenOrCreate','ReadWrite','None'); $probe.Dispose()\n"
        "[Console]::WriteLine((@{registered=$global:registered; repaired=$global:repaired;"
        " ensured=$global:ensured; failed=$failed} | ConvertTo-Json -Compress))\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual == {
        "registered": int(scenario in ("enabled", "repair")),
        "repaired": int(scenario.startswith("repair")),
        "ensured": int(scenario in ("preserve", "repair-absent")),
        "failed": scenario == "query-fails",
    }


def test_scheduler_has_not_run_result_is_read_through_guarded_helper():
    text = _text()
    assert "$ScheduledTaskHasNotRunResult = 267011" in text

    body = _function_body("Get-ScheduledTaskLastResult")
    assert "Get-ScheduledTaskInfo -TaskName $Name -ErrorAction Stop" in body
    assert "return [int64]$info.LastTaskResult" in body
    assert "catch" in body and "return $null" in body


@pytest.mark.skipif(os.name != "nt", reason="native task registration with isolated scheduler boundary")
@pytest.mark.parametrize("repair", [False, True])
def test_task_repair_restores_interactive_mode_without_changing_normal_provision(tmp_path, repair):
    state = tmp_path / "daemon-state"
    state.write_text("unchanged")
    script = tmp_path / "repair-mode.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir='{tmp_path}'; $LinkPython='{sys.executable}'; $PidFile='{tmp_path / 'pid'}'\n"
        "$TaskName='fixture'; $Action='provision'; $NonInteractive=$false\n"
        "$env:AGENT_BRIDGE_NONINTERACTIVE=''; $ScheduledTaskHasNotRunResult=267011\n"
        "$script:mode=''; $script:removed=0; $script:UseNonInteractive=$false\n"
        "function Write-Ok { param($m) }; function Write-Warn { param($m) throw $m }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) [pscustomobject]@{"
        " Principal=[pscustomobject]@{LogonType='S4U'};"
        " Actions=@([pscustomobject]@{Execute='old'; Arguments='old'; WorkingDirectory='old'})} }\n"
        "function Get-ScheduledTaskLastResult { param($Name) 0 }\n"
        "function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory)"
        " [pscustomobject]@{Execute=$Execute; Arguments=$Argument; WorkingDirectory=$WorkingDirectory} }\n"
        "function New-ScheduledTaskTrigger { param([switch]$AtStartup,[switch]$AtLogOn,$User)"
        " $script:mode=if ($AtStartup) {'S4U'} else {'Interactive'}; [pscustomobject]@{Delay=''} }\n"
        "function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) @{} }\n"
        "function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,"
        "[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit,$MultipleInstances) @{} }\n"
        "function Set-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Principal) }\n"
        "function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Description,[switch]$Force) }\n"
        "function Remove-RegisteredTask { param($Name) $script:removed++ }\n"
        "function Start-ScheduledTask { throw 'task start forbidden' }\n"
        "function Invoke-Start { throw 'direct ensure forbidden' }\n"
        + _function_body("Resolve-DaemonLogonMode")
        + _function_body("Register-ScheduledTask_")
        + f"\nRegister-ScheduledTask_ -ForceTaskRepair:${str(repair).lower()}\n"
        "[Console]::WriteLine((@{mode=$script:mode; removed=$script:removed} | ConvertTo-Json -Compress))\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "mode": "Interactive" if repair else "S4U", "removed": int(repair),
    }
    assert state.read_text() == "unchanged"


def test_explicit_noninteractive_selection_precedes_failed_task_recovery():
    body = _function_body("Resolve-DaemonLogonMode")
    explicit = body.index("if ($NonInteractive")
    task_result = body.index("$lastTaskResult = Get-ScheduledTaskLastResult")
    assert explicit < task_result, (
        "An explicit flag/env opt-in must keep intentional non-interactive installations"
    )


def test_never_run_s4u_task_does_not_override_interactive_default():
    body = _function_body("Resolve-DaemonLogonMode")
    failure = body.index("($existing.Principal.LogonType -eq 'S4U')")
    preservation = body.index("$Script:UseNonInteractive = $true", failure)
    recovery = body[failure:preservation]

    assert "$lastTaskResult -eq $ScheduledTaskHasNotRunResult" in recovery
    assert "selecting the default interactive AtLogOn mode" in recovery
    assert "return" in recovery


def test_requested_s4u_start_diagnoses_token_failure_and_recovery():
    body = _function_body("Invoke-Start")
    requested_start = body.index("Start-ScheduledTask -TaskName $TaskName")
    task_result = body.index(
        "$lastTaskResult = Get-ScheduledTaskLastResult", requested_start
    )
    diagnosis = body.index("SCHED_S_TASK_HAS_NOT_RUN", task_result)

    assert requested_start < task_result < diagnosis
    assert "could not acquire the S4U logon token" in body[diagnosis:]
    assert "default interactive AtLogOn task" in body[diagnosis:]


def test_provision_has_a_dedicated_runtime_preserving_path():
    text = _text()
    assert "'provision' { Invoke-Provision }" in text

    body = _function_body("Invoke-Provision")
    missing_runtime = body.index("if (-not $ForceTaskRepair -and -not (Test-Path $LinkPython))")
    install = body.index("Invoke-Install", missing_runtime)
    return_after_install = body.index("return", install)
    register = body.index("Register-ScheduledTask_", return_after_install)

    assert missing_runtime < install < return_after_install < register
    assert "Test-SlotContentCurrent" not in body
    assert "Invoke-Start" not in body
    assert "New-SignedVenv" not in body


def test_provision_routes_never_run_s4u_recovery_through_elevated_repair():
    body = _function_body("Invoke-Provision")
    explicit = body.index("$explicitNonInteractive")
    s4u = body.index("($existing.Principal.LogonType -eq 'S4U')")
    never_ran = body.index("$ScheduledTaskHasNotRunResult", s4u)
    repair = body.index("$repair = Get-ScheduledTaskRepairScript", never_ran)
    register = body.index("Register-ScheduledTask_", repair)
    release = body.index("finally { Exit-InstallLock }", register)
    child = body.index("& $pwsh", release)
    assert explicit < s4u < never_ran < repair < register < release < child
    assert "-not $isAdmin" in body
    assert "[Environment]::GetCommandLineArgs() -icontains '-NonInteractive'" in body
    assert "$repairHostArguments += '-NonInteractive'" in body
    assert "& $pwsh -NoProfile -ExecutionPolicy Bypass @repairHostArguments -File $repair" in body
    assert "if ($repairExit -ne 0)" in body
    dispatch = _text()[_text().index("$script:InstallerLifecycleAction ="):]
    assert "if ($Action -ne 'schedule' -and -not $ForceTaskRepair)" in dispatch
    assert "if ($Action -in @('install', 'update', 'provision') -and -not $ForceTaskRepair)" in dispatch


def test_unattended_repair_refuses_before_elevation_or_task_mutation():
    text = (_PLUGIN_ROOT / "scripts/repair-scheduled-task.ps1").read_text(encoding="utf-8")
    gate = text.index("if ($needsElevation -and -not (Test-IsAdmin))")
    refusal = text.index("unattended repair left the existing task unchanged", gate)
    elevation = text.index("Start-Process -FilePath $exe -Verb RunAs", gate)
    mutation = text.index("& $installer provision -InstallDir $InstallDir", gate)
    assert gate < refusal < elevation < mutation
    assert "[Environment]::GetCommandLineArgs() -icontains '-NonInteractive'" in text[gate:refusal]


def test_repair_executable_is_local_to_retained_native_installer():
    body = _function_body("Get-ScheduledTaskRepairScript")
    assert "Join-Path $PSScriptRoot 'repair-scheduled-task.ps1'" in body
    assert "COPILOT_PLUGIN_STAGED_FROM" not in body
    assert "System32\\WindowsPowerShell\\v1.0\\powershell.exe" in _function_body("Get-PwshPath")


@pytest.mark.skipif(os.name != "nt", reason="actual unattended Windows PowerShell repair")
def test_actual_unattended_repair_keeps_task_without_prompting(tmp_path):
    source = (_PLUGIN_ROOT / "scripts/repair-scheduled-task.ps1").read_text(encoding="utf-8")
    # Intercept only task access, the elevation capability and its launch.
    source = source.replace(
        "$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue",
        "$existing = [pscustomobject]@{Principal=[pscustomobject]@{LogonType='S4U'}}",
    )
    gate = "if ($needsElevation -and -not (Test-IsAdmin)) {"
    assert source.count(gate) == 1
    source = source.replace(
        gate,
        "function Test-IsAdmin { return $false }\n"
        "function Start-Process { throw 'fixture refuses any elevation launch' }\n" + gate,
    )
    path = tmp_path / "repair.ps1"
    path.write_text(source, encoding="utf-8")
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path),
         "-TaskName", "AgentBridge-fixture"],
        env={**os.environ, "AGENT_BRIDGE_CONFIG_DIR": str(tmp_path / "runtime")},
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "unattended repair left the existing task unchanged" in " ".join(result.stderr.split())
    assert "fixture refuses any elevation launch" not in result.stderr
    assert not (tmp_path / "runtime").exists()


@pytest.mark.skipif(os.name != "nt", reason="native scoped repair assessment with intercepted UAC and task access")
@pytest.mark.parametrize(
    "selection",
    ["scoped-broken", "scoped-healthy", "scoped-interactive-repair", "legacy-direct",
     "qualified-mismatch", "qualified-inferred", "scoped-unbound"],
)
def test_repair_assesses_only_selected_installation_and_retains_uac_selectors(tmp_path, selection):
    from agent_bridge.install_paths import scheduled_task_name

    home = tmp_path / "home"
    home.mkdir()
    root = home / ".agent-bridge" if selection == "legacy-direct" else tmp_path / "selected cell"
    root.mkdir()
    state = root / "state"
    state.write_text("unchanged")
    expected_name = "Agent Bridge" if selection == "legacy-direct" else scheduled_task_name(root)
    if selection not in ("legacy-direct", "scoped-unbound"):
        (root / "bin").mkdir()
        (root / "bin" / "resolve-runtime.ps1").write_text(
            f"$AgentRtPy='{sys.executable}'\n", encoding="utf-8-sig",
        )
    report = tmp_path / "assessment.json"
    source = (_PLUGIN_ROOT / "scripts" / "repair-scheduled-task.ps1").read_text(encoding="utf-8")
    query = "$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue"
    stubs = r"""
$script:assessment=@{queries=@(); elevations=0}
function Save-Assessment {
    [IO.File]::WriteAllText($env:FIXTURE_REPORT, ($script:assessment | ConvertTo-Json -Depth 6))
}
function Test-IsAdmin { $false }
function Get-ScheduledTask {
    param($TaskName,$ErrorAction)
    $script:assessment.queries += $TaskName
    Save-Assessment
    if ($TaskName -eq $env:FIXTURE_SELECTED_NAME) {
        $type=if ($env:FIXTURE_SELECTION -eq 'scoped-broken') {'S4U'} else {'Interactive'}
        $triggers=if ($env:FIXTURE_SELECTION -eq 'scoped-interactive-repair') {@()} else {
            @([pscustomobject]@{CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}})
        }
    } elseif ($TaskName -eq 'Agent Bridge') {
        $type=if ($env:FIXTURE_SELECTION -eq 'scoped-broken') {'Interactive'} else {'S4U'}
        $triggers=@([pscustomobject]@{CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}})
    } else { throw 'unexpected task query' }
    [pscustomobject]@{Principal=[pscustomobject]@{LogonType=$type}; Triggers=$triggers}
}
function Start-Process {
    param($FilePath,$Verb,[switch]$PassThru,[switch]$Wait,$ArgumentList)
    if ($Verb -ne 'RunAs') { throw 'unexpected process launch' }
    $script:assessment.elevations++
    $script:assessment.arguments=@($ArgumentList)
    Save-Assessment
    [pscustomobject]@{ExitCode=0}
}
function Start-ScheduledTask { throw 'task start forbidden' }
function Register-ScheduledTask { throw 'real task mutation forbidden' }
function Unregister-ScheduledTask { throw 'real task mutation forbidden' }
function Invoke-Start { throw 'direct ensure forbidden' }
"""
    assert source.count(query) == 1
    source = source.replace(query, stubs + "\n" + query)
    repair = tmp_path / "repair.ps1"
    repair.write_text(source, encoding="utf-8-sig")
    (tmp_path / "install.ps1").write_text(
        "param($Action,$InstallDir,[switch]$ForceTaskRepair)\n"
        "if ($Action -ne 'provision' -or -not $ForceTaskRepair) { throw 'invalid repair re-entry' }\n"
        "$result=Get-Content -LiteralPath $env:FIXTURE_REPORT -Raw | ConvertFrom-Json\n"
        "$result | Add-Member -NotePropertyName installDir -NotePropertyValue $InstallDir\n"
        "$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $env:FIXTURE_REPORT\n"
        "$global:LASTEXITCODE=0\n", encoding="utf-8-sig",
    )
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home),
           "FIXTURE_REPORT": str(report), "FIXTURE_SELECTION": selection,
           "FIXTURE_SELECTED_NAME": expected_name}
    for name in ("AGENT_BRIDGE_CONFIG_DIR", "AGENT_BRIDGE_INSTALL_DIR", "AGENT_RT_ROOT"):
        env.pop(name, None)
    argv = [shutil.which("powershell"), "-NoProfile", "-File", str(repair)]
    if selection != "legacy-direct":
        argv += ["-InstallDir", str(root)]
    if selection not in ("legacy-direct", "qualified-inferred", "scoped-unbound"):
        argv += ["-TaskName", "Agent Bridge" if selection == "qualified-mismatch" else expected_name]
    result = subprocess.run(
        argv, cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if selection in ("qualified-mismatch", "scoped-unbound"):
        assert result.returncode != 0, result.stdout + result.stderr
        assert not report.exists()
        assert "selector disagrees" in result.stderr or "requires its installation task name" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        actual = json.loads(report.read_text(encoding="utf-8-sig"))
        assert actual["queries"] == [expected_name]
        assert actual["elevations"] == int(selection == "scoped-broken")
        if selection == "scoped-broken":
            forwarded = actual["arguments"]
            assert forwarded[forwarded.index("-InstallDir") + 1] == f'"{root}"'
            assert forwarded[forwarded.index("-TaskName") + 1] == f'"{expected_name}"'
            assert "-Elevated" in forwarded
        elif selection == "scoped-interactive-repair":
            assert Path(actual["installDir"]) == root
    assert state.read_text() == "unchanged"
