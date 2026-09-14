"""Windows Scheduled Task management for agent-machines self-update."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .self_update_state import TIER_SPECS, tier_status


@dataclass
class ScheduledTaskSnapshot:
    task_name: str
    present: bool
    enabled: bool | None = None
    state: str | None = None
    logon_type: str | None = None
    description: str | None = None
    execute: str | None = None
    arguments: str | None = None
    working_directory: str | None = None
    trigger_kind: str | None = None
    trigger_value: int | None = None
    matching: bool = False


@dataclass
class ScheduledTaskReconcileResult:
    tier: str
    desired_state: str
    status: str
    changed: bool
    detail: str
    snapshot: ScheduledTaskSnapshot | None = None
    commands: list[list[str]] = field(default_factory=list)
    attempted_elevation: bool = False

    @property
    def ok(self) -> bool:
        return self.status not in {"error", "deferred"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "desired_state": self.desired_state,
            "status": self.status,
            "changed": self.changed,
            "ok": self.ok,
            "detail": self.detail,
            "snapshot": self.snapshot.__dict__ if self.snapshot is not None else None,
            "commands": self.commands,
            "attempted_elevation": self.attempted_elevation,
        }


@dataclass
class ScheduledTaskStatus:
    tier: str
    opted_in: bool
    task_name: str
    registered: bool
    enabled: bool | None = None
    matching: bool = False
    state: str | None = None
    detail: str = ""
    last_attempt: str | None = None
    last_success: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def runtime_root(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".agent-machines"


def task_runtime_python() -> str:
    return sys.executable


def task_description(tier: str) -> str:
    if tier == "watchdog":
        return "agent-machines unattended self-update watchdog (hourly dtssh liveness)"
    if tier == "sweep":
        return "agent-machines unattended self-update sweep (daily reconcile)"
    raise ValueError(f"unknown self-update tier: {tier}")


def task_action_arguments(tier: str, *, machine: str | None = None) -> str:
    parts = [
        f'--headless "{task_runtime_python()}" -m agent_machines self-update run --tier {tier}'
    ]
    if machine:
        parts.extend(["--machine", machine])
    return " ".join(parts)


def task_working_directory(home: Path | None = None) -> str:
    return str(runtime_root(home))


def install_retry_command(tier: str, *, machine: str | None = None) -> list[str]:
    command = ["agent-machines", "self-update", "install", "--tier", tier]
    if machine:
        command.extend(["--machine", machine])
    return command


def _normalize_windows_text(value: str | None) -> str:
    return (value or "").replace("/", "\\").casefold().strip()


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_binary(resolve_binary) -> str:
    binary = resolve_binary("pwsh") or resolve_binary("powershell")
    if binary is None:
        raise RuntimeError("pwsh or powershell is required to manage Scheduled Tasks")
    return binary


def _run_powershell(script: str, *, runner, resolve_binary, timeout: int = 300):
    return runner(
        [
            _powershell_binary(resolve_binary),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def task_definition_matches(
    snapshot: ScheduledTaskSnapshot,
    tier: str,
    *,
    machine: str | None = None,
    home: Path | None = None,
) -> bool:
    if not snapshot.present:
        return False
    spec = TIER_SPECS[tier]
    arguments = _normalize_windows_text(snapshot.arguments)
    execute = Path(snapshot.execute or "").name.casefold()
    return (
        execute == "conhost.exe"
        and (snapshot.logon_type or "") == "Interactive"
        and (snapshot.description or "") == task_description(tier)
        and _normalize_windows_text(task_runtime_python()) in arguments
        and "-m agent_machines self-update run" in arguments
        and f"--tier {tier}" in arguments
        and (
            (not machine and "--machine " not in arguments)
            or (machine and f"--machine {machine.casefold()}" in arguments)
        )
        and _normalize_windows_text(snapshot.working_directory)
        == _normalize_windows_text(task_working_directory(home))
        and (snapshot.trigger_kind or "") == spec.schedule_kind
        and snapshot.trigger_value == spec.schedule_value
    )


def _snapshot_from_payload(
    tier: str,
    payload: dict[str, Any],
    *,
    machine: str | None = None,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    snapshot = ScheduledTaskSnapshot(
        task_name=TIER_SPECS[tier].task_name,
        present=bool(payload.get("present")),
        enabled=payload.get("enabled") if isinstance(payload.get("enabled"), bool) else None,
        state=payload.get("state") if isinstance(payload.get("state"), str) else None,
        logon_type=payload.get("logon_type")
        if isinstance(payload.get("logon_type"), str)
        else None,
        description=payload.get("description")
        if isinstance(payload.get("description"), str)
        else None,
        execute=payload.get("execute") if isinstance(payload.get("execute"), str) else None,
        arguments=payload.get("arguments") if isinstance(payload.get("arguments"), str) else None,
        working_directory=payload.get("working_directory")
        if isinstance(payload.get("working_directory"), str)
        else None,
        trigger_kind=payload.get("trigger_kind")
        if isinstance(payload.get("trigger_kind"), str)
        else None,
        trigger_value=payload.get("trigger_value")
        if isinstance(payload.get("trigger_value"), int)
        else None,
    )
    snapshot.matching = task_definition_matches(snapshot, tier, machine=machine, home=home)
    return snapshot


def query_scheduled_task(
    tier: str,
    *,
    machine: str | None = None,
    runner,
    resolve_binary,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    task_name = TIER_SPECS[tier].task_name
    daily_trigger_type = (
        "Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskDailyTrigger"
    )
    script = f"""
if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
$task = Get-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction SilentlyContinue
if (-not $task) {{
  [ordered]@{{ present = $false }} | ConvertTo-Json -Compress
  exit 0
}}
$action = @($task.Actions)[0]
$trigger = @($task.Triggers)[0]
$enabled = $null
try {{
  $enabled = [bool]$task.Settings.Enabled
}} catch {{
  $enabled = [string]$task.State -ne 'Disabled'
}}
$triggerKind = $null
$triggerValue = $null
if ($trigger.PSObject.TypeNames -contains {_ps_literal(daily_trigger_type)}) {{
  $triggerKind = 'daily'
  $triggerValue = [int]$trigger.DaysInterval
}} elseif ($trigger.Repetition.Interval) {{
  $triggerKind = 'hourly'
  $triggerValue = 1
}}
[ordered]@{{
  present = $true
  enabled = $enabled
  state = [string]$task.State
  logon_type = [string]$task.Principal.LogonType
  description = [string]$task.Description
  execute = [string]$action.Execute
  arguments = [string]$action.Arguments
  working_directory = [string]$action.WorkingDirectory
  trigger_kind = $triggerKind
  trigger_value = $triggerValue
}} | ConvertTo-Json -Compress
""".strip()
    result = _run_powershell(script, runner=runner, resolve_binary=resolve_binary, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.output or f"failed to query Scheduled Task {task_name!r}")
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except ValueError as exc:
        raise RuntimeError(f"invalid Scheduled Task query response for {task_name!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid Scheduled Task query response for {task_name!r}")
    return _snapshot_from_payload(tier, payload, machine=machine, home=home)


def register_scheduled_task(
    tier: str,
    *,
    machine: str | None = None,
    runner,
    resolve_binary,
    home: Path | None = None,
):
    spec = TIER_SPECS[tier]
    task_name = spec.task_name
    schedule_script = (
        # Set repetition via New-ScheduledTaskTrigger's own -RepetitionInterval/
        # -RepetitionDuration parameters, not by mutating $trigger.Repetition
        # afterward: Get-ScheduledTask's CIM Repetition property is not a live
        # reference, so a post-hoc property assignment silently fails to
        # persist (Repetition.Interval/Duration read back empty after
        # registration, and the task never reports as 'matching').
        "$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(5)) "
        "-RepetitionInterval (New-TimeSpan -Hours 1) "
        "-RepetitionDuration (New-TimeSpan -Days 3650)"
        if spec.schedule_kind == "hourly"
        else "$trigger = New-ScheduledTaskTrigger -Daily -At '3:00AM' -DaysInterval 1"
    )
    script = f"""
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
$taskArgs = {_ps_literal(task_action_arguments(tier, machine=machine))}
$action = New-ScheduledTaskAction `
  -Execute 'conhost.exe' `
  -Argument $taskArgs `
  -WorkingDirectory {_ps_literal(task_working_directory(home))}
{schedule_script}
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -StartWhenAvailable
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
  -UserId $currentUser `
  -LogonType Interactive `
  -RunLevel Limited
Register-ScheduledTask `
  -TaskName {_ps_literal(task_name)} `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Force `
  -Description {_ps_literal(task_description(tier))} | Out-Null
Enable-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction SilentlyContinue | Out-Null
""".strip()
    return _run_powershell(script, runner=runner, resolve_binary=resolve_binary, timeout=300)


def enable_scheduled_task(tier: str, *, runner, resolve_binary):
    task_name = TIER_SPECS[tier].task_name
    script = f"""
if (-not (Get-Command Enable-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
Enable-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction Stop | Out-Null
""".strip()
    return _run_powershell(script, runner=runner, resolve_binary=resolve_binary, timeout=180)


def unregister_scheduled_task(tier: str, *, runner, resolve_binary):
    task_name = TIER_SPECS[tier].task_name
    script = f"""
if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
$task = Get-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction SilentlyContinue
if (-not $task) {{
  exit 0
}}
Stop-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction SilentlyContinue | Out-Null
Unregister-ScheduledTask `
  -TaskName {_ps_literal(task_name)} `
  -Confirm:$false `
  -ErrorAction Stop | Out-Null
""".strip()
    return _run_powershell(script, runner=runner, resolve_binary=resolve_binary, timeout=180)


def _looks_like_access_denied(text: str) -> bool:
    folded = text.casefold()
    return "access is denied" in folded or "0x80070005" in folded


def scheduled_task_retry_message(tier: str, *, existing: bool, machine: str | None = None) -> str:
    action = "update the existing Scheduled Task" if existing else "install the Scheduled Task"
    command = " ".join(install_retry_command(tier, machine=machine))
    return (
        f"Scheduled Task registration needs elevation -- run once from an elevated "
        f"PowerShell to {action}: {command}"
    )


def reconcile_scheduled_task(
    tier: str,
    *,
    desired_present: bool,
    machine: str | None = None,
    runner,
    resolve_binary,
    record_task_config,
    home: Path | None = None,
) -> ScheduledTaskReconcileResult:
    if sys.platform != "win32":
        return ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="present" if desired_present else "absent",
            status="skipped",
            changed=False,
            detail="Scheduled Task reconciliation is supported only on Windows",
        )
    snapshot = query_scheduled_task(
        tier,
        machine=machine,
        runner=runner,
        resolve_binary=resolve_binary,
        home=home,
    )
    desired_state = "present" if desired_present else "absent"
    try:
        if desired_present:
            if snapshot.present and snapshot.matching:
                if snapshot.enabled is False:
                    enabled = enable_scheduled_task(
                        tier, runner=runner, resolve_binary=resolve_binary
                    )
                    if enabled.returncode != 0:
                        raise RuntimeError(
                            enabled.output
                            or f"failed to enable Scheduled Task {snapshot.task_name!r}"
                        )
                    snapshot.enabled = True
                    record_task_config(
                        home, tier, installed=True, opted_in=True, attempted_elevation=False
                    )
                    return ScheduledTaskReconcileResult(
                        tier=tier,
                        desired_state=desired_state,
                        status="changed",
                        changed=True,
                        detail="enabled the existing Scheduled Task",
                        snapshot=snapshot,
                    )
                record_task_config(
                    home, tier, installed=True, opted_in=True, attempted_elevation=False
                )
                return ScheduledTaskReconcileResult(
                    tier=tier,
                    desired_state=desired_state,
                    status="ok",
                    changed=False,
                    detail="Scheduled Task is already registered",
                    snapshot=snapshot,
                )
            registered = register_scheduled_task(
                tier,
                machine=machine,
                runner=runner,
                resolve_binary=resolve_binary,
                home=home,
            )
            if registered.returncode == 0:
                current = query_scheduled_task(
                    tier,
                    machine=machine,
                    runner=runner,
                    resolve_binary=resolve_binary,
                    home=home,
                )
                record_task_config(
                    home, tier, installed=True, opted_in=True, attempted_elevation=False
                )
                return ScheduledTaskReconcileResult(
                    tier=tier,
                    desired_state=desired_state,
                    status="changed",
                    changed=True,
                    detail="registered the Scheduled Task",
                    snapshot=current,
                )
            if _looks_like_access_denied(registered.output):
                detail = scheduled_task_retry_message(
                    tier, existing=snapshot.present, machine=machine
                )
                record_task_config(
                    home, tier, installed=snapshot.present, opted_in=True, attempted_elevation=True
                )
                return ScheduledTaskReconcileResult(
                    tier=tier,
                    desired_state=desired_state,
                    status="deferred",
                    changed=False,
                    detail=detail,
                    snapshot=snapshot,
                    commands=[install_retry_command(tier, machine=machine)],
                    attempted_elevation=True,
                )
            raise RuntimeError(
                registered.output or f"failed to register Scheduled Task {snapshot.task_name!r}"
            )
        if not snapshot.present:
            record_task_config(
                home, tier, installed=False, opted_in=False, attempted_elevation=False
            )
            return ScheduledTaskReconcileResult(
                tier=tier,
                desired_state=desired_state,
                status="ok",
                changed=False,
                detail="Scheduled Task is already absent",
                snapshot=snapshot,
            )
        removed = unregister_scheduled_task(tier, runner=runner, resolve_binary=resolve_binary)
        if removed.returncode != 0:
            raise RuntimeError(
                removed.output or f"failed to remove Scheduled Task {snapshot.task_name!r}"
            )
        record_task_config(home, tier, installed=False, opted_in=False, attempted_elevation=False)
        snapshot.present = False
        snapshot.enabled = False
        snapshot.matching = False
        return ScheduledTaskReconcileResult(
            tier=tier,
            desired_state=desired_state,
            status="changed",
            changed=True,
            detail="removed the Scheduled Task",
            snapshot=snapshot,
        )
    except Exception as exc:
        return ScheduledTaskReconcileResult(
            tier=tier,
            desired_state=desired_state,
            status="error",
            changed=False,
            detail=str(exc),
            snapshot=snapshot,
        )


def scheduled_task_status(
    tier: str,
    *,
    opted_in: bool,
    machine: str | None = None,
    runner,
    resolve_binary,
    home: Path | None = None,
) -> ScheduledTaskStatus:
    observed = tier_status(home, tier)
    if sys.platform != "win32":
        return ScheduledTaskStatus(
            tier=tier,
            opted_in=opted_in,
            task_name=TIER_SPECS[tier].task_name,
            registered=False,
            enabled=None,
            matching=False,
            state=None,
            detail="Scheduled Tasks are supported only on Windows",
            last_attempt=observed.last_attempt,
            last_success=observed.last_success,
        )
    snapshot = query_scheduled_task(
        tier,
        machine=machine,
        runner=runner,
        resolve_binary=resolve_binary,
        home=home,
    )
    if not snapshot.present:
        detail = "Scheduled Task is not registered"
    elif snapshot.matching:
        detail = "Scheduled Task is registered"
    else:
        detail = "Scheduled Task is present but does not match the expected definition"
    return ScheduledTaskStatus(
        tier=tier,
        opted_in=opted_in,
        task_name=TIER_SPECS[tier].task_name,
        registered=snapshot.present,
        enabled=snapshot.enabled,
        matching=snapshot.matching,
        state=snapshot.state,
        detail=detail,
        last_attempt=observed.last_attempt,
        last_success=observed.last_success,
    )
