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
        ("absent", "custom", "refused"),
        ("absent", "suffix", "refused"),
        ("absent", "ambiguous", "refused"),
        ("absent", "disabled", "refused"),
        ("absent", "remove-fails", "refused"),
    ],
)
def test_scheduling_policy_is_task_only_and_repeatable(tmp_path, policy, task, expected):
    root = tmp_path / "bridge"
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
        "function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction)\n"
        " if ($script:kind -eq 'remove-fails') { throw 'access denied' }; "
        "$script:removed++; $script:task=$null }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) $script:task }\n"
        "if ($script:kind -ne 'missing') {\n"
        " $launcher=Join-Path $InstallDir 'start-agent-bridge.ps1'\n"
        " if ($script:kind -eq 'custom') { $launcher='C:\\foreign\\service.ps1' }\n"
        " if ($script:kind -eq 'suffix') { $launcher += '.foreign' }\n"
        " $actions=@([pscustomobject]@{Execute='conhost.exe'; Arguments=\"--headless powershell.exe -File `\"$launcher`\"\"})\n"
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
        "$ErrorActionPreference='Stop'; $script:calls=0\n"
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


def test_scheduler_has_not_run_result_is_read_through_guarded_helper():
    text = _text()
    assert "$ScheduledTaskHasNotRunResult = 267011" in text

    body = _function_body("Get-ScheduledTaskLastResult")
    assert "Get-ScheduledTaskInfo -TaskName $Name -ErrorAction Stop" in body
    assert "return [int64]$info.LastTaskResult" in body
    assert "catch" in body and "return $null" in body


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
    missing_runtime = body.index("if (-not (Test-Path $LinkPython))")
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

    assert explicit < s4u < never_ran < repair < register
    assert "[Environment]::GetCommandLineArgs() -icontains '-NonInteractive'" in body
    assert "$repairHostArguments += '-NonInteractive'" in body
    assert "& $pwsh -NoProfile -ExecutionPolicy Bypass @repairHostArguments -File $repair" in body
    assert "if ($repairExit -ne 0)" in body


def test_unattended_repair_refuses_before_elevation_or_task_mutation():
    text = (_PLUGIN_ROOT / "scripts/repair-scheduled-task.ps1").read_text(encoding="utf-8")
    gate = text.index("if ($needsElevation -and -not (Test-IsAdmin))")
    refusal = text.index("unattended repair left the existing task unchanged", gate)
    elevation = text.index("Start-Process -FilePath $exe -Verb RunAs", gate)
    mutation = text.index("Unregister-ScheduledTask -TaskName $TaskName", gate)
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
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)],
        env={**os.environ, "AGENT_BRIDGE_CONFIG_DIR": str(tmp_path / "runtime")},
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "unattended repair left the existing task unchanged" in " ".join(result.stderr.split())
    assert "fixture refuses any elevation launch" not in result.stderr
    assert not (tmp_path / "runtime").exists()
