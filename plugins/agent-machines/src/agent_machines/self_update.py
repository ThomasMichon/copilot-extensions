"""Tiered unattended self-update for agent-machines.
Two independently scheduled tiers keep a logged-in Windows machine converging
without an interactive Copilot session:
* ``watchdog``: ensure the dtssh host launcher process is running
  (``ensure_watchdog``), repair a stale or wedged dtssh host with the same
  identity-preserving primitive an operator runs by hand
  (``ensure_dtssh_host_healthy`` -> ``agent-ssh restore-host``; see
  copilot-extensions#3994 -- a launcher process can stay alive on an ancient,
  wedged snapshot indefinitely with nothing to notice or repair it), then
  reconcile this machine's outbound reach into the dtssh mesh (re-discover
  live tunnel ids, re-emit the SSH profile, verify every known alias). Any of
  the three failing stops the run before the next step runs.
* ``sweep``: fast-forward adopted repos, refresh plugin payloads/runtimes, then
  run the maintenance-safe machine restore subset.
The tiers resolve opt-in from declarative ``self-update`` resources, use one
named lock each, and record last-attempt / last-success timestamps under the
agent-machines state root.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import self_update_tasks as _tasks
from .self_update_dtssh import (
    DtsshConfig,
    ensure_dtssh_host_healthy,
    ensure_watchdog,
    refresh_dtssh_mesh,
)
from .self_update_dtssh import (
    default_launcher_starter as default_launcher_starter,
)
from .self_update_dtssh import (
    default_process_lister as default_process_lister,
)
from .self_update_lock import (
    TierLock,
    _iso_utc,
    _utc_now,
)
from .self_update_state import (
    TIER_SPECS,
    WATCHDOG_TIER,
    lock_path,
    record_task_config,
    write_status,
)
from .self_update_tasks import (
    ScheduledTaskReconcileResult,
    ScheduledTaskSnapshot,
    ScheduledTaskStatus,
)
from .self_update_tasks import linux_systemd_user_available as _linux_systemd_user_available
from .self_update_tasks import (
    query_scheduled_task as _query_scheduled_task,
)
from .self_update_tasks import (
    reconcile_scheduled_task as _reconcile_scheduled_task,
)
from .self_update_tasks import (
    scheduled_task_status as _scheduled_task_status,
)
from .self_update_types import (
    CommandResult,
    RunResult,
    StepResult,
    default_command_runner,
    shutil_which,
)

LIVE_SESSION_DEFER_STATUSES = {"awaiting-operator", "busy", "idle", "running"}

task_action_arguments = _tasks.task_action_arguments
task_description = _tasks.task_description
task_working_directory = _tasks.task_working_directory


def query_scheduled_task(
    tier: str,
    *,
    machine: str | None = None,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    return _query_scheduled_task(
        tier,
        machine=machine,
        runner=runner or default_command_runner,
        resolve_binary=shutil_which,
        home=home,
    )


def query_task_state(
    tier: str,
    *,
    machine: str | None = None,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskSnapshot:
    """Platform-dispatching query for the declarative resource's dry-run path.

    Unlike ``query_scheduled_task()`` above (always Windows Scheduled Tasks
    -- correct for its Windows-only callers), this mirrors
    ``reconcile_scheduled_task()``'s own ``sys.platform`` dispatch so a
    platform-agnostic caller gets the systemd --user timer state on
    Linux/WSL instead of probing for `pwsh`/`Get-ScheduledTask`. Mirrors the
    identical fleet_update.py fix.
    """
    resolved_runner = runner or default_command_runner
    if sys.platform == "linux":
        # Guard mirrors _reconcile_linux_timer/_linux_timer_status's own check.
        if not _linux_systemd_user_available(resolve_binary=shutil_which, runner=resolved_runner):
            return _tasks.ScheduledTaskSnapshot(
                task_name=_tasks._linux_timer_name(tier), present=False, unavailable=True
            )
        return _tasks.query_systemd_timer(
            tier,
            machine=machine,
            runner=resolved_runner,
            resolve_binary=shutil_which,
            home=home,
        )
    return _query_scheduled_task(
        tier,
        machine=machine,
        runner=resolved_runner,
        resolve_binary=shutil_which,
        home=home,
    )


def reconcile_scheduled_task(
    tier: str,
    *,
    desired_present: bool,
    machine: str | None = None,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskReconcileResult:
    return _reconcile_scheduled_task(
        tier,
        desired_present=desired_present,
        machine=machine,
        runner=runner or default_command_runner,
        resolve_binary=shutil_which,
        record_task_config=record_task_config,
        home=home,
    )


def scheduled_task_status(
    tier: str,
    *,
    opted_in: bool,
    machine: str | None = None,
    runner: Callable[..., CommandResult] | None = None,
    home: Path | None = None,
) -> ScheduledTaskStatus:
    return _scheduled_task_status(
        tier,
        opted_in=opted_in,
        machine=machine,
        runner=runner or default_command_runner,
        resolve_binary=shutil_which,
        home=home,
    )


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
    bare_check = runner(["git", "rev-parse", "--is-bare-repository"], cwd=repo, timeout=120)
    if bare_check.returncode != 0:
        return StepResult(
            "git-pull", "error", bare_check.output or "git rev-parse failed", path=str(repo)
        )
    if bare_check.stdout.strip() == "true":
        return _fast_forward_bare_repo(repo, runner=runner)

    status = runner(["git", "status", "--porcelain"], cwd=repo, timeout=120)
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


def _fast_forward_bare_repo(
    repo: Path, *, runner: Callable[..., CommandResult]
) -> StepResult:
    """Fast-forward a bare repository's checked-out branch ref directly.

    A bare repository (an agent-worktrees anchor where all real work happens
    in separate linked worktrees) has no working tree or index for ``git
    status``/``git pull`` to act on. This fetches the upstream branch into a
    private scratch ref -- rather than trusting a plain ``git fetch``, which
    would honor whatever fetch refspec the repo is configured with (a
    mirror-style bare clone can carry ``+refs/*:refs/*``, which force-writes
    straight into ``refs/heads/*`` and would silently clobber the branch
    before any safety check below runs) -- then only moves the real branch
    ref forward, as an atomic compare-and-swap, once every fast-forward
    check has passed against that isolated fetch result.
    """
    branch = runner(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo, timeout=120)
    if branch.returncode != 0:
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the bare repository's HEAD is detached",
            path=str(repo),
        )
    branch_name = branch.stdout.strip()

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
    remote, _, remote_branch = upstream.stdout.strip().partition("/")
    if not remote or not remote_branch:
        return StepResult(
            "git-pull",
            "error",
            f"could not parse upstream ref {upstream.stdout.strip()!r}",
            path=str(repo),
        )

    old_sha = runner(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, timeout=120)
    if old_sha.returncode != 0:
        return StepResult(
            "git-pull", "error", old_sha.output or "git rev-parse HEAD failed", path=str(repo)
        )
    old_sha_value = old_sha.stdout.strip()

    scratch_ref = f"refs/agent-machines/self-update-fetch/{branch_name}"

    def _cleanup_scratch() -> None:
        runner(["git", "update-ref", "-d", scratch_ref], cwd=repo, timeout=120)

    fetched = runner(
        [
            "git",
            "fetch",
            "--quiet",
            "--no-tags",
            remote,
            f"+refs/heads/{remote_branch}:{scratch_ref}",
        ],
        cwd=repo,
        timeout=900,
    )
    if fetched.returncode != 0:
        _cleanup_scratch()
        return StepResult(
            "git-pull", "error", fetched.output or "git fetch failed", path=str(repo)
        )

    new_sha = runner(["git", "rev-parse", "--verify", scratch_ref], cwd=repo, timeout=120)
    if new_sha.returncode != 0:
        _cleanup_scratch()
        return StepResult(
            "git-pull",
            "error",
            new_sha.output or "git rev-parse fetched upstream failed",
            path=str(repo),
        )
    new_sha_value = new_sha.stdout.strip()

    counts = runner(
        ["git", "rev-list", "--left-right", "--count", f"{old_sha_value}...{new_sha_value}"],
        cwd=repo,
        timeout=120,
    )
    if counts.returncode != 0:
        _cleanup_scratch()
        return StepResult(
            "git-pull", "error", counts.output or "git rev-list failed", path=str(repo)
        )
    ahead, behind = _parse_git_counts(counts.stdout)
    if ahead > 0 and behind > 0:
        _cleanup_scratch()
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the checkout is diverged",
            path=str(repo),
        )
    if ahead > 0:
        _cleanup_scratch()
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the checkout is ahead of upstream",
            path=str(repo),
        )
    if behind == 0:
        _cleanup_scratch()
        return StepResult("git-pull", "ok", "checkout is already up to date", path=str(repo))

    # Defense in depth: old_sha_value/new_sha_value are both already-resolved
    # object ids by this point, so this is deterministic rather than a race
    # check, but it costs nothing to re-prove the fast-forward immediately
    # before the write.
    ancestry = runner(
        ["git", "merge-base", "--is-ancestor", old_sha_value, new_sha_value],
        cwd=repo,
        timeout=120,
    )
    if ancestry.returncode != 0:
        _cleanup_scratch()
        return StepResult(
            "git-pull",
            "skipped",
            "skipped fast-forward pull because the upstream moved to a "
            "non-fast-forward commit during the update",
            path=str(repo),
        )

    updated = runner(
        ["git", "update-ref", f"refs/heads/{branch_name}", new_sha_value, old_sha_value],
        cwd=repo,
        timeout=120,
    )
    _cleanup_scratch()
    if updated.returncode != 0:
        return StepResult(
            "git-pull", "error", updated.output or "git update-ref failed", path=str(repo)
        )
    return StepResult(
        "git-pull", "changed", "fast-forwarded bare repository branch ref", path=str(repo)
    )



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
    mesh_refresher: Callable[[], StepResult] | None = None,
    host_healer: Callable[[], StepResult] | None = None,
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
            healer = host_healer or (lambda: ensure_dtssh_host_healthy(runner=runner))
            host_health_step = healer()
            steps.append(host_health_step)
            if host_health_step.status == "error":
                return RunResult(
                    tier=tier,
                    status="error",
                    opted_in=True,
                    detail=host_health_step.detail,
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=steps,
                )
            refresher = mesh_refresher or (lambda: refresh_dtssh_mesh(runner=runner))
            mesh_step = refresher()
            steps.append(mesh_step)
            if mesh_step.status == "error":
                return RunResult(
                    tier=tier,
                    status="error",
                    opted_in=True,
                    detail=mesh_step.detail,
                    lock_reclaimed=lock.reclaimed,
                    attempted_at=attempted_at,
                    steps=steps,
                )
        else:
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
                        "update",
                        "--no-manager",
                    ],
                    timeout=3600,
                )
                steps.append(
                    StepResult(
                        f"update:{repo_name}",
                        "changed" if plugin_refresh.returncode == 0 else "error",
                        plugin_refresh.output or "updated plugin payloads and runtimes",
                        command=plugin_refresh.argv,
                    )
                )
                if plugin_refresh.returncode != 0:
                    return RunResult(
                        tier=tier,
                        status="error",
                        opted_in=True,
                        detail=plugin_refresh.output or "agent-worktrees update failed",
                        lock_reclaimed=lock.reclaimed,
                        attempted_at=attempted_at,
                        steps=steps,
                    )
            restore_cmd = [
                "agent-machines",
                "restore",
                "--apply",
                "--all-projects",
                "--maintenance-safe",
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
    except Exception as exc:
        # Every anticipated failure above already returns its own `RunResult`
        # with status="error" from inside the `try`. This is a backstop for
        # anything NOT anticipated (a bug in a step, an OSError resolving a
        # project path, ...): without it, an uncaught exception here would
        # propagate past `run_tier` entirely, crashing the whole `self-update
        # run` CLI invocation with a raw traceback instead of a clean,
        # structured error result -- the lock is still released by `finally`
        # below and `last_attempt` is still recorded (written above, before
        # any step runs), but the caller would otherwise get an unhandled
        # exception instead of a normal `RunResult` it can log/report.
        return RunResult(
            tier=tier,
            status="error",
            opted_in=True,
            detail=f"unexpected error: {exc}",
            lock_reclaimed=lock.reclaimed,
            attempted_at=locals().get("attempted_at"),
            steps=locals().get("steps", []),
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
