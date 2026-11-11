"""Native Scheduled Task definitions with the shared mutation admission guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .scheduler_retirement import windows_mutation_guard_script


def windows_registration_script(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
    existing_machine: str | None = None,
) -> str:
    spec = tasks.TIER_SPECS[tier]
    # Repetition must be declared on the native constructor, not a CIM property copy.
    schedule = (
        "$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(5)) "
        "-RepetitionInterval (New-TimeSpan -Hours 1) "
        "-RepetitionDuration (New-TimeSpan -Days 3650)"
        if spec.schedule_kind == "hourly"
        else "$trigger = New-ScheduledTaskTrigger -Daily -At "
        + ("'4:00AM'" if kind == "fleet" else "'3:00AM'")
        + " -DaysInterval 1"
    )
    arguments = (
        tasks.task_action_arguments(tier, home=home, machine=machine)
        if kind == "self"
        else tasks.task_action_arguments(tier, home=home)
    )
    literal = tasks._ps_literal
    guard = windows_mutation_guard_script(
        kind, tasks, tier, home=home,
        machine=existing_machine if kind == "self" else machine,
    )
    return (
        guard
        + "\n"
        + f"""
$action = New-ScheduledTaskAction `
  -Execute 'conhost.exe' `
  -Argument {literal(arguments)} `
  -WorkingDirectory {literal(tasks.task_working_directory(home))}
{schedule}
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
  -TaskName {literal(spec.task_name)} `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Force `
  -Description {literal(tasks.task_description(tier))} | Out-Null
Enable-ScheduledTask -TaskName {literal(spec.task_name)} -ErrorAction Stop | Out-Null
""".strip()
    )
