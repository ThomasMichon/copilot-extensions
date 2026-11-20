"""OS timer backends for ``scheduled-job`` resources.

Windows registers a per-user Scheduled Task and Linux/WSL a ``systemd
--user`` service + timer, both invoking ``agent-machines scheduled-job run
<id>`` headlessly through the stable binstub, mirroring the self-update
backends in :mod:`self_update_tasks`. Other platforms are unsupported.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .scheduled_jobs import jobs_root
from .self_update_tasks import (
    _linux_unit_dir,
    _ps_literal,
    _run_powershell,
    _systemd_quote,
    linux_systemd_user_available,
    task_binstub_path,
)

if TYPE_CHECKING:
    from .scheduled_jobs import BinaryResolver, Runner
    from .self_update_types import CommandResult

TASK_PREFIX = "agent-machines-scheduled-job-"


@dataclass
class TaskState:
    present: bool
    matching: bool = False


def task_name(job_id: str) -> str:
    return f"{TASK_PREFIX}{job_id}"


def backend_label(platform: str) -> str:
    return "systemd --user timer" if platform in ("linux", "wsl") else "Scheduled Task"


def unsupported_reason(
    platform: str, *, runner: Runner, resolve_binary: BinaryResolver
) -> str | None:
    if platform == "windows":
        return None
    if platform in ("linux", "wsl"):
        if linux_systemd_user_available(resolve_binary=resolve_binary, runner=runner):
            return None
        return "no reachable systemd --user manager on this host"
    return f"scheduled-job is not supported on platform {platform!r} (Windows and Linux only)"


def _description(job_id: str, interval: int, timeout: int) -> str:
    return f"agent-machines scheduled job '{job_id}' every {interval}s, timeout {timeout}s"


def _command_parts(job_id: str, home: Path | None) -> list[str]:
    return [task_binstub_path(home), "scheduled-job", "run", job_id]


def _windows_arguments(job_id: str, home: Path | None) -> str:
    return f"--headless cmd.exe /c {subprocess.list2cmdline(_command_parts(job_id, home))}"


def _unit_paths(job_id: str, home: Path | None) -> tuple[Path, Path]:
    unit_dir = _linux_unit_dir(home)
    return unit_dir / f"{task_name(job_id)}.service", unit_dir / f"{task_name(job_id)}.timer"


def render_units(
    job_id: str, interval: int, timeout: int, home: Path | None = None
) -> tuple[str, str]:
    base = home if home is not None else Path.home()
    system_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    path_value = f"PATH={base / '.local' / 'bin'}:{system_path}"
    description = _description(job_id, interval, timeout)
    service = (
        f"[Unit]\nDescription={description}\n\n[Service]\nType=oneshot\n"
        f"WorkingDirectory={jobs_root(home)}\n"
        f"Environment={_systemd_quote(path_value)}\n"
        f"TimeoutStartSec={timeout}\n"
        f"ExecStart={' '.join(_systemd_quote(part) for part in _command_parts(job_id, home))}\n"
    )
    timer = (
        f"[Unit]\nDescription={description}\n\n[Timer]\nOnBootSec=5min\n"
        f"OnUnitActiveSec={interval}s\nPersistent=false\n\n[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


def query(
    platform: str,
    job_id: str,
    interval: int,
    timeout: int,
    *,
    runner: Runner,
    resolve_binary: BinaryResolver,
    home: Path | None = None,
) -> TaskState:
    if platform in ("linux", "wsl"):
        paths = _unit_paths(job_id, home)
        if not all(path.exists() for path in paths):
            return TaskState(present=any(path.exists() for path in paths))
        enabled = runner(
            ["systemctl", "--user", "is-enabled", f"{task_name(job_id)}.timer"], timeout=30
        )
        actual = tuple(path.read_text(encoding="utf-8") for path in paths)
        return TaskState(
            True,
            enabled.stdout.strip() == "enabled"
            and actual == render_units(job_id, interval, timeout, home),
        )
    script = f"""
if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
$task = Get-ScheduledTask -TaskName {_ps_literal(task_name(job_id))} -ErrorAction SilentlyContinue
if (-not $task) {{ [ordered]@{{ present = $false }} | ConvertTo-Json -Compress; exit 0 }}
[ordered]@{{
  present = $true
  enabled = [string]$task.State -ne 'Disabled'
  description = [string]$task.Description
  arguments = [string]@($task.Actions)[0].Arguments
}} | ConvertTo-Json -Compress
""".strip()
    result = _run_powershell(script, runner=runner, resolve_binary=resolve_binary, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            result.output or f"failed to query Scheduled Task {task_name(job_id)!r}"
        )
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except ValueError as exc:
        raise RuntimeError(
            f"invalid Scheduled Task query response for {task_name(job_id)!r}"
        ) from exc
    if not isinstance(payload, dict) or not payload.get("present"):
        return TaskState(present=False)
    return TaskState(
        True,
        payload.get("enabled") is True
        and payload.get("description") == _description(job_id, interval, timeout)
        and str(payload.get("arguments") or "").strip().casefold()
        == _windows_arguments(job_id, home).casefold(),
    )


def register(
    platform: str,
    job_id: str,
    interval: int,
    timeout: int,
    *,
    runner: Runner,
    resolve_binary: BinaryResolver,
    home: Path | None = None,
) -> CommandResult:
    jobs_root(home).mkdir(parents=True, exist_ok=True)
    if platform in ("linux", "wsl"):
        service_path, timer_path = _unit_paths(job_id, home)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service, timer = render_units(job_id, interval, timeout, home)
        service_path.write_text(service, encoding="utf-8")
        timer_path.write_text(timer, encoding="utf-8")
        reloaded = runner(["systemctl", "--user", "daemon-reload"], timeout=60)
        if reloaded.returncode != 0:
            return reloaded
        return runner(
            ["systemctl", "--user", "enable", "--now", f"{task_name(job_id)}.timer"], timeout=60
        )
    name = _ps_literal(task_name(job_id))
    description = _ps_literal(_description(job_id, interval, timeout))
    # No -StartWhenAvailable: missed runs are not caught up. IgnoreNew keeps a
    # slow run from overlapping the next trigger; the run itself enforces the
    # declared timeout, and ExecutionTimeLimit is only a backstop.
    script = f"""
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
$action = New-ScheduledTaskAction -Execute 'conhost.exe' `
  -Argument {_ps_literal(_windows_arguments(job_id, home))} `
  -WorkingDirectory {_ps_literal(str(jobs_root(home)))}
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(5)) `
  -RepetitionInterval (New-TimeSpan -Seconds {int(interval)}) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Seconds {int(timeout) + 300}) -MultipleInstances IgnoreNew
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive `
  -RunLevel Limited
Register-ScheduledTask -TaskName {name} -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Force -Description {description} | Out-Null
Enable-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue | Out-Null
""".strip()
    registered: CommandResult = _run_powershell(
        script, runner=runner, resolve_binary=resolve_binary, timeout=300
    )
    return registered


def unregister(
    platform: str,
    job_id: str,
    *,
    runner: Runner,
    resolve_binary: BinaryResolver,
    home: Path | None = None,
    stop: bool = True,
) -> CommandResult:
    """Remove one job's timer; ``stop=False`` when the caller is that job's own run."""
    if platform in ("linux", "wsl"):
        # Best effort: a unit never loaded has nothing to disable. Stopping
        # the timer does not stop a service run already in progress.
        runner(
            ["systemctl", "--user", "disable", "--now", f"{task_name(job_id)}.timer"], timeout=60
        )
        for path in _unit_paths(job_id, home):
            if path.exists():
                path.unlink()
        return runner(["systemctl", "--user", "daemon-reload"], timeout=60)
    name = _ps_literal(task_name(job_id))
    stop_line = (
        f"Stop-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue | Out-Null\n"
        if stop
        else ""
    )
    script = f"""
if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {{
  throw 'ScheduledTasks cmdlets are unavailable'
}}
if (-not (Get-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue)) {{ exit 0 }}
{stop_line}Unregister-ScheduledTask -TaskName {name} -Confirm:$false -ErrorAction Stop | Out-Null
""".strip()
    removed: CommandResult = _run_powershell(
        script, runner=runner, resolve_binary=resolve_binary, timeout=180
    )
    return removed
