"""Unattended fleet-update for agent-machines.

A single scheduled tier -- ``sweep`` -- that runs ``worktree-manager update``
on a daily cadence, so the fleet's plugin install/update orchestration
happens asynchronously on a schedule rather than inline during a session.
Deliberately much simpler than ``self_update.py``'s tiers: the actual work
here is a single subprocess call to a binstub the Worktree Manager already
owns end-to-end -- this module only adds the opt-in/locking/scheduling
envelope around that one call, the same envelope ``self_update.py`` uses for
its own tiers.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from . import fleet_update_tasks as _tasks
from .fleet_update_lock import TierLock, _iso_utc, _utc_now
from .fleet_update_state import (
    SWEEP_TIER,
    TIER_SPECS,
    lock_path,
    record_task_config,
    write_status,
)
from .fleet_update_tasks import (
    ScheduledTaskReconcileResult,
    ScheduledTaskSnapshot,
    ScheduledTaskStatus,
)
from .fleet_update_tasks import (
    query_scheduled_task as _query_scheduled_task,
)
from .fleet_update_tasks import (
    reconcile_scheduled_task as _reconcile_scheduled_task,
)
from .fleet_update_tasks import (
    scheduled_task_status as _scheduled_task_status,
)

task_action_arguments = _tasks.task_action_arguments
task_description = _tasks.task_description
task_working_directory = _tasks.task_working_directory


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


def query_scheduled_task(
    tier: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    return _query_scheduled_task(
        tier,
        runner=runner or default_command_runner,
        resolve_binary=shutil_which,
        home=home,
    )


def query_task_state(
    tier: str,
    *,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    """Platform-dispatching query for the declarative resource's dry-run path.

    Unlike ``query_scheduled_task()`` above (always the Windows Scheduled
    Task query -- correct for the Windows-only register/reconcile call sites
    that use it directly), this mirrors ``reconcile_scheduled_task()``'s own
    internal ``sys.platform`` dispatch so a platform-agnostic caller (the
    resource handler's ``apply(dry_run=True)``) gets the systemd --user timer
    state on Linux/WSL instead of unconditionally probing for `pwsh`/
    `Get-ScheduledTask`.
    """
    resolved_runner = runner or default_command_runner
    if sys.platform == "linux":
        return _tasks.query_systemd_timer(
            tier, runner=resolved_runner, resolve_binary=shutil_which, home=home
        )
    return _query_scheduled_task(
        tier, runner=resolved_runner, resolve_binary=shutil_which, home=home
    )


def reconcile_scheduled_task(
    tier: str,
    *,
    desired_present: bool,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskReconcileResult:
    return _reconcile_scheduled_task(
        tier,
        desired_present=desired_present,
        runner=runner or default_command_runner,
        resolve_binary=shutil_which,
        record_task_config=record_task_config,
        home=home,
    )


def scheduled_task_status(
    tier: str,
    *,
    opted_in: bool,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskStatus:
    return _scheduled_task_status(
        tier,
        opted_in=opted_in,
        runner=runner or default_command_runner,
        resolve_binary=shutil_which,
        home=home,
    )


def default_command_runner(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
) -> CommandResult:
    # See self_update.default_command_runner for why argv[0] is resolved
    # through PATH/PATHEXT before invoking (a `.cmd`/`.bat` binstub shim
    # otherwise raises FileNotFoundError under CreateProcess).
    resolved = argv
    if argv:
        binary = shutil.which(argv[0])
        if binary:
            resolved = [binary, *argv[1:]]
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        resolved,
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


def run_tier(
    tier: str,
    *,
    opted_in: bool,
    runner: Callable[..., CommandResult] = default_command_runner,
    home: Path | None = None,
) -> RunResult:
    if tier not in TIER_SPECS:
        raise ValueError(f"unknown fleet-update tier: {tier}")
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
        # tier == SWEEP_TIER is the only tier today (see fleet_update_state);
        # the branch is spelled out for symmetry with self_update.run_tier's
        # per-tier shape, so a second tier can be added the same way later.
        if tier == SWEEP_TIER:
            update_result = runner(["worktree-manager", "update"], timeout=3600)
            steps = [
                StepResult(
                    "update",
                    "changed" if update_result.returncode == 0 else "error",
                    update_result.output or "ran worktree-manager update",
                    command=update_result.argv,
                )
            ]
            if update_result.returncode != 0:
                return RunResult(
                    tier=tier,
                    status="error",
                    opted_in=True,
                    detail=update_result.output or "worktree-manager update failed",
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=steps,
                )
        else:  # pragma: no cover - no other tier exists yet
            raise ValueError(f"unhandled fleet-update tier: {tier}")
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
        f"fleet-update {result.tier}: {result.status}",
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
    return shutil.which(binary)
