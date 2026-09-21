"""Tiered unattended self-update for agent-machines.
Two independently scheduled tiers keep a logged-in Windows machine converging
without an interactive Copilot session:
* ``watchdog``: ensure the dtssh host launcher watchdog is running, and
  reconcile this machine's outbound reach into the dtssh mesh (re-discover
  live tunnel ids, re-emit the SSH profile, verify every known alias).
* ``sweep``: fast-forward adopted repos, refresh plugin payloads/runtimes, then
  run the maintenance-safe machine restore subset.
The tiers resolve opt-in from declarative ``self-update`` resources, use one
named lock each, and record last-attempt / last-success timestamps under the
agent-machines state root.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys  # noqa: F401 - tests patch self_update.sys.platform directly
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from . import _peer_launch
from . import self_update_tasks as _tasks
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
from .self_update_tasks import (
    query_scheduled_task as _query_scheduled_task,
)
from .self_update_tasks import (
    reconcile_scheduled_task as _reconcile_scheduled_task,
)
from .self_update_tasks import (
    scheduled_task_status as _scheduled_task_status,
)

WATCHDOG_START_TIMEOUT_SECONDS = 40
LIVE_SESSION_DEFER_STATUSES = {"awaiting-operator", "busy", "idle", "running"}

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


@dataclass(frozen=True)
class DtsshConfig:
    config_path: Path
    install_root: Path
    launcher_path: Path
    alias: str
    port: int
    tunnel: str | None = None
    user: str | None = None


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


def default_command_runner(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
) -> CommandResult:
    # Resolve argv[0] through PATH/PATHEXT before invoking: Windows subprocess
    # creation (CreateProcess, used when shell=False) does not apply PATHEXT
    # resolution the way cmd.exe does, so a bare command name that is really a
    # `.cmd`/`.bat` shim (e.g. the `agent-worktrees` binstub) raises
    # FileNotFoundError / WinError 2 even though it is genuinely on PATH.
    # shutil.which() performs the same PATHEXT-aware search cmd.exe does, so
    # resolving here fixes every unattended self-update caller uniformly.
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
    # No `**no_window_kwargs()` here: unlike an ordinary child, this argv's
    # target IS conhost.exe itself, and CREATE_NO_WINDOW on conhost.exe --
    # rather than on the process it hosts -- breaks its ability to allocate
    # the headless console the pwsh launcher child needs. With that flag set,
    # conhost.exe exits immediately (rc 0) without ever starting pwsh, so the
    # launcher silently never comes up and every caller times out waiting for
    # it. `--headless` already keeps the hosted console window from
    # appearing, so no extra creation flag is needed on this parent.
    subprocess.Popen(  # noqa: S603 - argv list, detached child
        argv,
        cwd=str(config.install_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
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


def _agent_ssh_argv_prefix() -> list[str] | None:
    """Resolve the invocation prefix for the optional agent-ssh peer.

    Under an explicit same-cell installation context, this validates the
    owner and resolves agent-ssh as a same-cell peer via the shared
    ``libs/peer-launch`` boundary (agent-ssh is itself a canonical
    installation-context adopter). A validated owner with no same-cell
    agent-ssh installation is a genuine, optional absence -- not every
    machine's cell installs agent-ssh -- and returns ``None`` so the caller
    reports ``skipped``, matching legacy ambient-PATH behavior. A malformed
    or refused context (as opposed to a clean absence) still raises
    ``ContextRefused``: an explicit context that fails to validate must
    never quietly degrade to "not installed".
    """
    raw = os.environ.get(_peer_launch.CONTEXT_ENV, "")
    if not raw:
        exe = shutil.which("agent-ssh")  # marketplace-isolation: allow legacy-compatibility
        return None if exe is None else [exe]
    own_root = Path(
        os.environ.get("AGENT_RT_ROOT", "~/.agent-machines")
    ).expanduser()
    try:
        own = _peer_launch.validate_owner("agent-machines", own_root, raw)
    except (OSError, ValueError, ImportError) as error:
        raise _peer_launch.ContextRefused(
            f"agent-machines installation context refused: {error}"
        ) from error
    peer = Path(own["cellRoot"]) / "plugins" / "agent-ssh"
    if not peer.exists() and not peer.is_symlink():
        return None
    return _peer_launch.launch_prefix(
        "agent-machines", Path(own["pluginRoot"]), raw, "agent-ssh",
    )


def refresh_dtssh_mesh(
    *, runner: Callable[..., CommandResult] = default_command_runner
) -> StepResult:
    """Reconcile this machine's outbound reach into the dtssh mesh.

    Delegates to ``agent-ssh refresh-mesh``, which re-discovers live tunnel
    ids, re-emits the managed SSH profile from that live state, and verifies
    reachability to every ``machines.yaml`` dtssh alias. This closes the gap
    where the watchdog tier only checked that *this* machine's own dtssh host
    launcher was alive, never that every other machine could still be reached
    -- a cached tunnel id going stale after a peer's host restart otherwise
    breaks inbound SSH silently until an operator notices
    (copilot-extensions#2684).

    Missing ``agent-ssh`` or no declared mesh is reported as ``skipped``, not
    an error: not every machine participates in a dtssh mesh.
    """
    prefix = _agent_ssh_argv_prefix()
    if prefix is None:
        return StepResult(
            "dtssh-mesh-refresh", "skipped", "agent-ssh is not installed on this machine"
        )
    result = runner([*prefix, "refresh-mesh", "--json"], timeout=300)
    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("machines_yaml") is None:
        return StepResult(
            "dtssh-mesh-refresh",
            "skipped",
            payload.get("detail") or "no machines.yaml found for this machine",
        )
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if result.returncode == 0:
        return StepResult(
            "dtssh-mesh-refresh",
            "ok",
            detail or "refreshed the dtssh mesh",
            command=result.argv,
        )
    return StepResult(
        "dtssh-mesh-refresh",
        "error",
        detail or result.output or "dtssh mesh refresh failed",
        command=result.argv,
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
