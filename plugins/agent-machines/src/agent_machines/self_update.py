"""Tiered unattended self-update for agent-machines.
Two independently scheduled tiers keep a logged-in Windows machine converging
without an interactive Copilot session:
* ``watchdog``: ensure the dtssh host launcher watchdog is running.
* ``sweep``: fast-forward adopted repos, refresh plugin payloads/runtimes, then
  run the full machine restore.
The tiers resolve opt-in from declarative ``self-update`` resources, use one
named lock each, and record last-attempt / last-success timestamps under the
agent-machines state root.
"""

from __future__ import annotations

import ctypes
import dataclasses
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from .self_update_state import (
    STATE_VERSION,
    TierStatus,
    load_status,
    lock_path,
    mutex_name,
    state_root,
    status_path,
    task_config,
    task_config_path,
    tier_status,
)

WATCHDOG_TIER = "watchdog"
SWEEP_TIER = "sweep"
WATCHDOG_STALE_SECONDS = 10 * 60
SWEEP_STALE_SECONDS = 3 * 60 * 60
WATCHDOG_START_TIMEOUT_SECONDS = 40
LIVE_SESSION_DEFER_STATUSES = {"awaiting-operator", "busy", "idle", "running"}


@dataclass(frozen=True)
class TierSpec:
    tier: str
    stale_seconds: int
    task_name: str
    schedule_kind: str
    schedule_value: int


TIER_SPECS: dict[str, TierSpec] = {
    WATCHDOG_TIER: TierSpec(
        tier=WATCHDOG_TIER,
        stale_seconds=WATCHDOG_STALE_SECONDS,
        task_name="agent-machines-self-update-watchdog",
        schedule_kind="hourly",
        schedule_value=1,
    ),
    SWEEP_TIER: TierSpec(
        tier=SWEEP_TIER,
        stale_seconds=SWEEP_STALE_SECONDS,
        task_name="agent-machines-self-update-sweep",
        schedule_kind="daily",
        schedule_value=1,
    ),
}


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        text = "\n".join(
            part.rstrip() for part in (self.stdout, self.stderr) if part and part.strip()
        ).strip()
        return text


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    command: list[str] | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.command:
            payload["command"] = self.command
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass
class RunResult:
    tier: str
    status: str
    opted_in: bool
    detail: str = ""
    lock_reclaimed: bool = False
    attempted_at: str | None = None
    success_at: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "noop"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "status": self.status,
            "ok": self.ok,
            "opted_in": self.opted_in,
            "detail": self.detail,
            "lock_reclaimed": self.lock_reclaimed,
            "attempted_at": self.attempted_at,
            "success_at": self.success_at,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class DtsshConfig:
    config_path: Path
    install_root: Path
    launcher_path: Path
    alias: str
    port: int
    tunnel: str | None = None
    user: str | None = None


@dataclass
class LockSnapshot:
    pid: int | None
    started_at: str | None
    age_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


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
    matching: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


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
            "snapshot": self.snapshot.to_dict() if self.snapshot is not None else None,
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
        return dataclasses.asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def runtime_root(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".agent-machines"


def runtime_python(home: Path | None = None) -> Path:
    return runtime_root(home) / ".venv" / "Scripts" / "python.exe"


def task_description(tier: str) -> str:
    if tier == WATCHDOG_TIER:
        return "agent-machines unattended self-update watchdog (hourly dtssh liveness)"
    if tier == SWEEP_TIER:
        return "agent-machines unattended self-update sweep (daily reconcile)"
    raise ValueError(f"unknown self-update tier: {tier}")


def task_action_arguments(tier: str, home: Path | None = None) -> str:
    python = str(runtime_python(home))
    return f'--headless "{python}" -m agent_machines self-update run --tier {tier}'


def task_working_directory(home: Path | None = None) -> str:
    return str(runtime_root(home))


def install_retry_command(tier: str) -> list[str]:
    return ["agent-machines", "self-update", "install", "--tier", tier]


def _iso_utc(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_windows_text(value: str | None) -> str:
    return (value or "").replace("/", "\\").casefold().strip()


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell_binary() -> str:
    binary = shutil_which("pwsh") or shutil_which("powershell")
    if binary is None:
        raise RuntimeError("pwsh or powershell is required to manage Scheduled Tasks")
    return binary


def run_powershell(
    script: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
    timeout: int = 300,
) -> CommandResult:
    runner = runner or default_command_runner
    return runner(
        [powershell_binary(), "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


def _task_snapshot_from_payload(
    tier: str,
    payload: dict[str, Any],
    *,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    snapshot = ScheduledTaskSnapshot(
        task_name=TIER_SPECS[tier].task_name,
        present=bool(payload.get("present")),
        enabled=payload.get("enabled") if isinstance(payload.get("enabled"), bool) else None,
        state=payload.get("state") if isinstance(payload.get("state"), str) else None,
        logon_type=(
            payload.get("logon_type") if isinstance(payload.get("logon_type"), str) else None
        ),
        description=(
            payload.get("description") if isinstance(payload.get("description"), str) else None
        ),
        execute=payload.get("execute") if isinstance(payload.get("execute"), str) else None,
        arguments=payload.get("arguments") if isinstance(payload.get("arguments"), str) else None,
        working_directory=(
            payload.get("working_directory")
            if isinstance(payload.get("working_directory"), str)
            else None
        ),
    )
    snapshot.matching = task_definition_matches(snapshot, tier, home=home)
    return snapshot


def task_definition_matches(
    snapshot: ScheduledTaskSnapshot,
    tier: str,
    *,
    home: Path | None = None,
) -> bool:
    if not snapshot.present:
        return False
    expected_execute = "conhost.exe"
    expected_description = task_description(tier)
    expected_python = _normalize_windows_text(str(runtime_python(home)))
    expected_workdir = _normalize_windows_text(task_working_directory(home))
    arguments = _normalize_windows_text(snapshot.arguments)
    execute = Path(snapshot.execute or "").name.casefold()
    return (
        execute == expected_execute
        and (snapshot.logon_type or "") == "Interactive"
        and (snapshot.description or "") == expected_description
        and expected_python in arguments
        and "-m agent_machines self-update run" in arguments
        and f"--tier {tier}" in arguments
        and _normalize_windows_text(snapshot.working_directory) == expected_workdir
    )


def query_scheduled_task(
    tier: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    task_name = TIER_SPECS[tier].task_name
    script = f"""
$task = Get-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction SilentlyContinue
if (-not $task) {{
  [ordered]@{{ present = $false }} | ConvertTo-Json -Compress
  exit 0
}}
$action = @($task.Actions)[0]
$enabled = $null
try {{
  $enabled = [bool]$task.Settings.Enabled
}} catch {{
  $enabled = [string]$task.State -ne 'Disabled'
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
}} | ConvertTo-Json -Compress
""".strip()
    result = run_powershell(script, runner=runner, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.output or f"failed to query Scheduled Task {task_name!r}")
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except ValueError as exc:
        raise RuntimeError(f"invalid Scheduled Task query response for {task_name!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid Scheduled Task query response for {task_name!r}")
    return _task_snapshot_from_payload(tier, payload, home=home)


def register_scheduled_task(
    tier: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> CommandResult:
    spec = TIER_SPECS[tier]
    task_name = spec.task_name
    description = task_description(tier)
    python = str(runtime_python(home))
    working_directory = task_working_directory(home)
    schedule_script = (
        "$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddMinutes(5)); "
        "$trigger.Repetition.Interval = (New-TimeSpan -Hours 1); "
        "$trigger.Repetition.Duration = (New-TimeSpan -Days 3650)"
        if spec.schedule_kind == "hourly"
        else "$trigger = New-ScheduledTaskTrigger -Daily -At '3:00AM' -DaysInterval 1"
    )
    script = f"""
$python = {_ps_literal(python)}
$taskArgs = '--headless "' + $python + '" -m agent_machines self-update run --tier {tier}'
$action = New-ScheduledTaskAction `
  -Execute 'conhost.exe' `
  -Argument $taskArgs `
  -WorkingDirectory {_ps_literal(working_directory)}
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
  -Description {_ps_literal(description)} | Out-Null
Enable-ScheduledTask -TaskName {_ps_literal(task_name)} -ErrorAction SilentlyContinue | Out-Null
""".strip()
    return run_powershell(script, runner=runner, timeout=300)


def enable_scheduled_task(
    tier: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
) -> CommandResult:
    task_name = TIER_SPECS[tier].task_name
    script = (
        f"Enable-ScheduledTask -TaskName {_ps_literal(task_name)} "
        "-ErrorAction Stop | Out-Null"
    )
    return run_powershell(script, runner=runner, timeout=180)


def unregister_scheduled_task(
    tier: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
) -> CommandResult:
    task_name = TIER_SPECS[tier].task_name
    script = f"""
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
    return run_powershell(script, runner=runner, timeout=180)


def _looks_like_access_denied(text: str) -> bool:
    folded = text.casefold()
    return "access is denied" in folded or "0x80070005" in folded


def scheduled_task_retry_message(tier: str, *, existing: bool) -> str:
    action = "update the existing Scheduled Task" if existing else "install the Scheduled Task"
    command = " ".join(install_retry_command(tier))
    return (
        f"Scheduled Task registration needs elevation -- run once from an elevated "
        f"PowerShell to {action}: {command}"
    )


def reconcile_scheduled_task(
    tier: str,
    *,
    desired_present: bool,
    runner: Callable[..., CommandResult] | None = None,
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
    runner = runner or default_command_runner
    snapshot = query_scheduled_task(tier, runner=runner, home=home)
    desired_state = "present" if desired_present else "absent"
    try:
        if desired_present:
            if snapshot.present and snapshot.matching:
                if snapshot.enabled is False:
                    enabled = enable_scheduled_task(tier, runner=runner)
                    if enabled.returncode != 0:
                        raise RuntimeError(
                            enabled.output
                            or f"failed to enable Scheduled Task {snapshot.task_name!r}"
                        )
                    snapshot.enabled = True
                    record_task_config(
                        home,
                        tier,
                        installed=True,
                        opted_in=True,
                        attempted_elevation=False,
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
                    home,
                    tier,
                    installed=True,
                    opted_in=True,
                    attempted_elevation=False,
                )
                return ScheduledTaskReconcileResult(
                    tier=tier,
                    desired_state=desired_state,
                    status="ok",
                    changed=False,
                    detail="Scheduled Task is already registered",
                    snapshot=snapshot,
                )
            registered = register_scheduled_task(tier, runner=runner, home=home)
            if registered.returncode == 0:
                current = query_scheduled_task(tier, runner=runner, home=home)
                record_task_config(
                    home,
                    tier,
                    installed=True,
                    opted_in=True,
                    attempted_elevation=False,
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
                detail = scheduled_task_retry_message(tier, existing=snapshot.present)
                record_task_config(
                    home,
                    tier,
                    installed=snapshot.present,
                    opted_in=True,
                    attempted_elevation=True,
                )
                return ScheduledTaskReconcileResult(
                    tier=tier,
                    desired_state=desired_state,
                    status="deferred",
                    changed=False,
                    detail=detail,
                    snapshot=snapshot,
                    commands=[install_retry_command(tier)],
                    attempted_elevation=True,
                )
            raise RuntimeError(
                registered.output or f"failed to register Scheduled Task {snapshot.task_name!r}"
            )
        if not snapshot.present:
            record_task_config(
                home,
                tier,
                installed=False,
                opted_in=False,
                attempted_elevation=False,
            )
            return ScheduledTaskReconcileResult(
                tier=tier,
                desired_state=desired_state,
                status="ok",
                changed=False,
                detail="Scheduled Task is already absent",
                snapshot=snapshot,
            )
        removed = unregister_scheduled_task(tier, runner=runner)
        if removed.returncode != 0:
            raise RuntimeError(
                removed.output or f"failed to remove Scheduled Task {snapshot.task_name!r}"
            )
        record_task_config(
            home,
            tier,
            installed=False,
            opted_in=False,
            attempted_elevation=False,
        )
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
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskStatus:
    if sys.platform != "win32":
        observed = tier_status(home, tier)
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
    runner = runner or default_command_runner
    snapshot = query_scheduled_task(tier, runner=runner, home=home)
    observed = tier_status(home, tier)
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


def write_status(
    home: Path | None, tier: str, *, attempt: str | None = None, success: str | None = None
) -> TierStatus:
    mutex = (
        _WindowsMutex("Global\\AgentMachinesSelfUpdateStatus")
        if sys.platform == "win32"
        else None
    )
    if mutex is not None:
        state = mutex.try_acquire()
        if state not in {"acquired", "abandoned"}:
            raise RuntimeError("could not acquire the self-update status lock")
    try:
        root = state_root(home)
        root.mkdir(parents=True, exist_ok=True)
        path = status_path(home)
        payload = load_status(home)
        tiers = payload.setdefault("tiers", {})
        current = tiers.setdefault(tier, {})
        if attempt is not None:
            current["last_attempt"] = attempt
        if success is not None:
            current["last_success"] = success
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
        return tier_status(home, tier)
    finally:
        if mutex is not None:
            mutex.release()
            mutex.close()

def record_task_config(
    home: Path | None,
    tier: str,
    *,
    installed: bool,
    opted_in: bool,
    attempted_elevation: bool,
) -> None:
    mutex = (
        _WindowsMutex("Global\\AgentMachinesSelfUpdateTaskConfig")
        if sys.platform == "win32"
        else None
    )
    if mutex is not None:
        state = mutex.try_acquire()
        if state not in {"acquired", "abandoned"}:
            raise RuntimeError("could not acquire the self-update task-config lock")
    try:
        root = state_root(home)
        root.mkdir(parents=True, exist_ok=True)
        path = task_config_path(home)
        payload = task_config(home)
        tiers = payload.setdefault("tiers", {})
        tiers[tier] = {
            "installed": installed,
            "opted_in": opted_in,
            "attempted_elevation": attempted_elevation,
            "updated_at": _iso_utc(_utc_now()),
        }
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if mutex is not None:
            mutex.release()
            mutex.close()

def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _read_lock_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_lock_record(path: Path, tier: str, pid: int, started_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STATE_VERSION,
        "tier": tier,
        "pid": pid,
        "started_at": started_at,
    }
    temp = path.with_name(f".{path.name}.{pid}.tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def lock_snapshot(
    tier: str, home: Path | None = None, *, now: datetime | None = None
) -> LockSnapshot | None:
    record = _read_lock_record(lock_path(tier, home))
    if record is None:
        return None
    started = _parse_iso_utc(record.get("started_at"))
    current = now or _utc_now()
    age = (current - started).total_seconds() if started is not None else None
    pid_value = record.get("pid")
    pid = pid_value if isinstance(pid_value, int) and pid_value > 0 else None
    return LockSnapshot(pid=pid, started_at=record.get("started_at"), age_seconds=age)


class _WindowsMutex:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    INFINITE = 0xFFFFFFFF

    def __init__(self, name: str):
        self.name = name
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise OSError(f"CreateMutexW failed for {name}")
        self.acquired = False

    def try_acquire(self) -> str:
        result = ctypes.windll.kernel32.WaitForSingleObject(self.handle, 0)
        if result == self.WAIT_OBJECT_0:
            self.acquired = True
            return "acquired"
        if result == self.WAIT_ABANDONED:
            self.acquired = True
            return "abandoned"
        if result == self.WAIT_TIMEOUT:
            return "timeout"
        raise OSError(f"WaitForSingleObject failed for {self.name}: {result}")

    def release(self) -> None:
        if self.acquired:
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            self.acquired = False

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


class TierLock(AbstractContextManager["TierLock"]):
    def __init__(
        self,
        *,
        tier: str,
        home: Path | None = None,
        pid: int | None = None,
        now: Callable[[], datetime] = _utc_now,
        pid_alive: Callable[[int | None], bool] = _pid_alive,
    ):
        self.spec = TIER_SPECS[tier]
        self.tier = tier
        self.home = home
        self.pid = pid if pid is not None else os.getpid()
        self._now = now
        self._pid_alive = pid_alive
        self._mutex: _WindowsMutex | None = None
        self.reclaimed = False
        self.started_at: str | None = None
        self.snapshot: LockSnapshot | None = None

    def acquire(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "self-update locking is supported only on Windows"
        self._mutex = _WindowsMutex(mutex_name(self.tier))
        try:
            state = self._mutex.try_acquire()
        except Exception:
            self._mutex.close()
            self._mutex = None
            raise
        if state == "timeout":
            self.snapshot = lock_snapshot(self.tier, self.home, now=self._now())
            self.release()
            detail = "another run is already active"
            if self.snapshot is not None and self.snapshot.pid:
                detail += f" (pid {self.snapshot.pid})"
            return False, detail
        path = lock_path(self.tier, self.home)
        record = _read_lock_record(path)
        current = self._now()
        if record is not None:
            existing_pid = record.get("pid")
            started = _parse_iso_utc(record.get("started_at"))
            age = (current - started).total_seconds() if started is not None else None
            if (
                isinstance(existing_pid, int)
                and existing_pid > 0
                and existing_pid != self.pid
                and self._pid_alive(existing_pid)
            ):
                self.snapshot = LockSnapshot(
                    pid=existing_pid,
                    started_at=record.get("started_at"),
                    age_seconds=age,
                )
                self.release()
                return False, f"another run is already active (pid {existing_pid})"
            if (
                isinstance(existing_pid, int)
                and existing_pid > 0
                and existing_pid != self.pid
                and age is not None
                and age < self.spec.stale_seconds
            ):
                self.snapshot = LockSnapshot(
                    pid=existing_pid,
                    started_at=record.get("started_at"),
                    age_seconds=age,
                )
                self.release()
                minutes = int(self.spec.stale_seconds / 60)
                return (
                    False,
                    f"previous run died but its {minutes}-minute stale window has not elapsed",
                )
            self.reclaimed = record.get("pid") not in (None, self.pid)
        self.started_at = _iso_utc(current)
        if self.started_at is None:
            raise RuntimeError("could not encode the lock start timestamp")
        _write_lock_record(path, self.tier, self.pid, self.started_at)
        return True, "acquired"

    def release(self) -> None:
        if self.started_at is not None:
            path = lock_path(self.tier, self.home)
            record = _read_lock_record(path)
            if (
                isinstance(record, dict)
                and record.get("pid") == self.pid
                and record.get("started_at") == self.started_at
            ):
                try:
                    path.unlink()
                except OSError:
                    pass
        self.started_at = None
        if self._mutex is not None:
            self._mutex.release()
            self._mutex.close()
            self._mutex = None

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
        return None


def default_command_runner(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
) -> CommandResult:
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
    return CommandResult(
        argv=list(argv), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )


def default_launcher_starter(config: DtsshConfig) -> bool:
    pwsh = shutil_which("pwsh")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    conhost = Path(system_root) / "System32" / "conhost.exe"
    if pwsh is None or not conhost.is_file():
        raise RuntimeError("pwsh and conhost.exe are required to start the dtssh launcher")
    argv = [
        str(conhost),
        "--headless",
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(config.launcher_path),
        "-Alias",
        config.alias,
        "-Port",
        str(config.port),
    ]
    if config.tunnel:
        argv.extend(["-Tunnel", config.tunnel])
    if config.user:
        argv.extend(["-User", config.user])
    subprocess.Popen(  # noqa: S603 - argv list, detached child
        argv,
        cwd=str(config.install_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    return True


def default_process_lister() -> list[dict[str, Any]]:
    pwsh = shutil_which("pwsh")
    if pwsh is None:
        raise RuntimeError("pwsh is required to inspect dtssh launcher liveness")
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='pwsh.exe'\" "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine } | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    result = default_command_runner(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script], timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(result.output or "cannot inspect running PowerShell processes")
    text = result.stdout.strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def default_worktree_lister() -> list[dict[str, Any]]:
    result = default_command_runner(
        [
            "agent-worktrees",
            "-p",
            "copilot-extensions",
            "list",
            "--json",
            "--fresh",
            "--tracking-status",
            "active",
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.output or "agent-worktrees list failed")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("agent-worktrees list returned invalid JSON")
    worktrees = payload.get("worktrees", [])
    if not isinstance(worktrees, list):
        raise RuntimeError("agent-worktrees list returned no worktrees list")
    return [item for item in worktrees if isinstance(item, dict)]


def _dtssh_config_path(local_app_data: str | None = None) -> Path:
    base = local_app_data or os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(base).expanduser().resolve() / "agent-ssh-dtssh" / "dispatch-companion.json"


def load_dtssh_config(local_app_data: str | None = None) -> DtsshConfig:
    path = _dtssh_config_path(local_app_data)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"dtssh companion config is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("dtssh companion config is invalid")
    alias = payload.get("alias")
    port = payload.get("port")
    if not isinstance(alias, str) or not alias.strip():
        raise RuntimeError("dtssh companion config needs a non-empty alias")
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
        raise RuntimeError("dtssh companion config needs a positive port")
    install_root = path.parent
    launcher = install_root / "dtssh-host-launcher.ps1"
    if not launcher.is_file():
        raise RuntimeError(f"dtssh launcher is missing: {launcher}")
    return DtsshConfig(
        config_path=path,
        install_root=install_root,
        launcher_path=launcher,
        alias=alias,
        port=port,
        tunnel=payload.get("tunnel") if isinstance(payload.get("tunnel"), str) else None,
        user=payload.get("user") if isinstance(payload.get("user"), str) else None,
    )


def watchdog_running(
    config: DtsshConfig,
    *,
    process_lister: Callable[[], list[dict[str, Any]]] = default_process_lister,
) -> bool:
    needle = str(config.launcher_path).replace("/", "\\").casefold()
    for proc in process_lister():
        pid = proc.get("ProcessId")
        cmd = proc.get("CommandLine")
        if not isinstance(pid, int) or pid <= 0 or not isinstance(cmd, str):
            continue
        if needle in cmd.replace("/", "\\").casefold():
            return True
    return False


def ensure_watchdog(
    *,
    process_lister: Callable[[], list[dict[str, Any]]] = default_process_lister,
    launcher_starter: Callable[[DtsshConfig], bool] = default_launcher_starter,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> list[StepResult]:
    config = load_dtssh_config()
    if watchdog_running(config, process_lister=process_lister):
        return [
            StepResult(
                name="dtssh-launcher",
                status="ok",
                detail="dtssh host launcher is already running",
                path=str(config.launcher_path),
            )
        ]
    launcher_starter(config)
    deadline = now() + WATCHDOG_START_TIMEOUT_SECONDS
    while now() < deadline:
        if watchdog_running(config, process_lister=process_lister):
            return [
                StepResult(
                    name="dtssh-launcher",
                    status="changed",
                    detail="started dtssh host launcher",
                    path=str(config.launcher_path),
                )
            ]
        sleeper(2)
    raise RuntimeError(
        f"dtssh host launcher did not report running within {WATCHDOG_START_TIMEOUT_SECONDS}s"
    )


def _parse_git_counts(output: str) -> tuple[int, int]:
    fields = output.strip().split()
    if len(fields) < 2:
        raise RuntimeError(f"unexpected git rev-list output: {output!r}")
    ahead = int(fields[0])
    behind = int(fields[1])
    return ahead, behind


def fast_forward_repo(
    repo: Path, *, runner: Callable[..., CommandResult] = default_command_runner
) -> StepResult:
    status = runner(
        ["git", "status", "--porcelain"], cwd=repo, timeout=120
    )
    if status.returncode != 0:
        return StepResult(
            "git-pull", "error", status.output or "git status failed", path=str(repo)
        )
    if status.stdout.strip():
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the checkout is dirty",
            path=str(repo),
        )
    upstream = runner(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo,
        timeout=120,
    )
    if upstream.returncode != 0:
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the checkout has no upstream branch",
            path=str(repo),
        )
    fetched = runner(["git", "fetch", "--quiet"], cwd=repo, timeout=900)
    if fetched.returncode != 0:
        return StepResult(
            "git-pull", "error", fetched.output or "git fetch failed", path=str(repo)
        )
    counts = runner(
        ["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=repo, timeout=120
    )
    if counts.returncode != 0:
        return StepResult(
            "git-pull", "error", counts.output or "git rev-list failed", path=str(repo)
        )
    ahead, behind = _parse_git_counts(counts.stdout)
    if ahead > 0 and behind > 0:
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the checkout is diverged",
            path=str(repo),
        )
    if ahead > 0:
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the checkout is ahead of upstream",
            path=str(repo),
        )
    if behind == 0:
        return StepResult("git-pull", "ok", "checkout is already up to date", path=str(repo))
    pulled = runner(["git", "pull", "--ff-only", "--no-rebase", "--quiet"], cwd=repo, timeout=1800)
    if pulled.returncode != 0:
        return StepResult("git-pull", "error", pulled.output or "git pull failed", path=str(repo))
    return StepResult("git-pull", "changed", "fast-forwarded checkout", path=str(repo))


def sweep_repo_paths(
    discovered_repos: list[Any],
    *,
    runner: Callable[..., CommandResult] = default_command_runner,
) -> list[StepResult]:
    steps: list[StepResult] = []
    seen: set[str] = set()
    for repo in discovered_repos:
        path = getattr(repo, "path", None)
        if not isinstance(path, Path):
            continue
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        steps.append(fast_forward_repo(path.resolve(), runner=runner))
    return steps


def live_session_deferral_reason(
    *,
    worktree_lister: Callable[[], list[dict[str, Any]]] = default_worktree_lister,
) -> str | None:
    try:
        worktrees = worktree_lister()
    except Exception as exc:
        return f"cannot safely determine live worktree state: {exc}"
    for worktree in worktrees:
        live_rest = str(worktree.get("live_rest") or "").casefold()
        if live_rest in LIVE_SESSION_DEFER_STATUSES:
            label = worktree.get("title") or worktree.get("summary") or worktree.get("id")
            return f"live session is active in worktree {label}"
    return None


def run_tier(
    tier: str,
    *,
    opted_in: bool,
    machine: str | None = None,
    discovered_repos: list[Any] | None = None,
    runner: Callable[..., CommandResult] = default_command_runner,
    process_lister: Callable[[], list[dict[str, Any]]] = default_process_lister,
    launcher_starter: Callable[[DtsshConfig], bool] = default_launcher_starter,
    worktree_lister: Callable[[], list[dict[str, Any]]] = default_worktree_lister,
    home: Path | None = None,
) -> RunResult:
    if tier not in TIER_SPECS:
        raise ValueError(f"unknown self-update tier: {tier}")
    if not opted_in:
        return RunResult(
            tier=tier,
            status="noop",
            opted_in=False,
            detail=f"tier {tier!r} is not opted in",
        )
    lock = TierLock(tier=tier, home=home)
    acquired, detail = lock.acquire()
    if not acquired:
        return RunResult(
            tier=tier,
            status="deferred",
            opted_in=True,
            detail=detail,
            steps=[
                StepResult(
                    "lock",
                    "deferred",
                    detail,
                    path=str(lock_path(tier, home)),
                )
            ],
        )
    try:
        attempted_at = _iso_utc(_utc_now())
        if attempted_at is None:
            raise RuntimeError("could not encode the attempt timestamp")
        write_status(home, tier, attempt=attempted_at)
        if tier == WATCHDOG_TIER:
            steps = ensure_watchdog(
                process_lister=process_lister,
                launcher_starter=launcher_starter,
            )
        else:
            defer = live_session_deferral_reason(worktree_lister=worktree_lister)
            if defer is not None:
                return RunResult(
                    tier=tier,
                    status="deferred",
                    opted_in=True,
                    detail=defer,
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=[StepResult("pre-mutation", "deferred", defer)],
                )
            steps = list(sweep_repo_paths(discovered_repos or [], runner=runner))
            pull_errors = [step for step in steps if step.status == "error"]
            if pull_errors:
                return RunResult(
                    tier=tier,
                    status="error",
                    opted_in=True,
                    detail=pull_errors[0].detail or "repository pull failed",
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=steps,
                )
            defer = live_session_deferral_reason(worktree_lister=worktree_lister)
            if defer is not None:
                return RunResult(
                    tier=tier,
                    status="deferred",
                    opted_in=True,
                    detail=defer,
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=[*steps, StepResult("pre-restore", "deferred", defer)],
                )
            repo_names = [
                getattr(repo, "name", "")
                for repo in (discovered_repos or [])
                if getattr(repo, "name", "")
            ]
            if not repo_names:
                repo_names = ["copilot-extensions"]
            for repo_name in sorted(set(repo_names)):
                plugin_refresh = runner(
                    [
                        "agent-worktrees",
                        "-p",
                        repo_name,
                        "reconcile-plugins",
                        "--apply",
                        "--with-payload-refresh",
                    ],
                    timeout=3600,
                )
                steps.append(
                    StepResult(
                        f"reconcile-plugins:{repo_name}",
                        "changed" if plugin_refresh.returncode == 0 else "error",
                        plugin_refresh.output or "reconciled plugin payloads and runtimes",
                        command=plugin_refresh.argv,
                    )
                )
                if plugin_refresh.returncode != 0:
                    return RunResult(
                        tier=tier,
                        status="error",
                        opted_in=True,
                        detail=plugin_refresh.output or "agent-worktrees reconcile-plugins failed",
                        lock_reclaimed=lock.reclaimed,
                        attempted_at=attempted_at,
                        steps=steps,
                    )
                defer = live_session_deferral_reason(worktree_lister=worktree_lister)
                if defer is not None:
                    return RunResult(
                        tier=tier,
                        status="deferred",
                        opted_in=True,
                        detail=defer,
                        lock_reclaimed=lock.reclaimed,
                        attempted_at=attempted_at,
                        steps=[*steps, StepResult("pre-restore", "deferred", defer)],
                    )
            restore_cmd = [
                sys.executable,
                "-m",
                "agent_machines",
                "restore",
                "--apply",
                "--all-projects",
            ]
            if machine:
                restore_cmd.extend(["--machine", machine])
            restore_result = runner(restore_cmd, timeout=7200)
            steps.append(
                StepResult(
                    "restore",
                    "changed" if restore_result.returncode == 0 else "error",
                    restore_result.output or "restored machine state",
                    command=restore_result.argv,
                )
            )
            if restore_result.returncode != 0:
                return RunResult(
                    tier=tier,
                    status="error",
                    opted_in=True,
                    detail=restore_result.output or "agent-machines restore failed",
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=steps,
                )
        success_at = _iso_utc(_utc_now())
        status = write_status(home, tier, success=success_at)
        return RunResult(
            tier=tier,
            status="ok",
            opted_in=True,
            detail="completed successfully",
            lock_reclaimed=lock.reclaimed,
            attempted_at=attempted_at,
            success_at=status.last_success,
            steps=steps,
        )
    finally:
        lock.release()


def format_result(result: RunResult) -> str:
    lines = [
        f"self-update {result.tier}: {result.status}",
    ]
    if result.detail:
        lines.append(f"  {result.detail}")
    if result.lock_reclaimed:
        lines.append("  reclaimed a stale prior lock")
    if result.attempted_at:
        lines.append(f"  last-attempt: {result.attempted_at}")
    if result.success_at:
        lines.append(f"  last-success: {result.success_at}")
    for step in result.steps:
        label = step.name
        suffix = f" ({step.path})" if step.path else ""
        lines.append(f"  - {label}: {step.status}{suffix}")
        if step.detail:
            lines.append(f"      {step.detail}")
        if step.command:
            lines.append(f"      $ {' '.join(step.command)}")
    return "\n".join(lines)


def format_plan_summary(summary: str, observed: dict[str, Any] | None) -> str:
    if not observed:
        return summary
    details = []
    if observed.get("last_attempt"):
        details.append(f"last-attempt={observed['last_attempt']}")
    if observed.get("last_success"):
        details.append(f"last-success={observed['last_success']}")
    if not details:
        return summary
    return f"{summary}; {'; '.join(details)}"


def shutil_which(binary: str) -> str | None:
    import shutil

    return shutil.which(binary)
