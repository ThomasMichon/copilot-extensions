"""dtssh host/mesh liveness for the unattended self-update ``watchdog`` tier.

Three cooperating checks, run in this order by ``self_update.run_tier``:

1. ``ensure_watchdog`` -- is *a* launcher process alive at all (starts one if
   entirely absent). This alone cannot tell a current, healthy host from a
   launcher process that has been running an ancient, wedged snapshot for
   weeks -- it only checks that some process's command line matches the
   launcher path.
2. ``ensure_dtssh_host_healthy`` -- does the running host actually serve an
   SSH banner, and is it current? Previews with ``agent-ssh restore-host
   --json`` and applies only when unhealthy: the automated counterpart of
   what an operator runs by hand today (the ``performing-machine-maintenance``
   skill's target-side drain: preview through the owning system, apply only
   through the normal owner, verify the stated postcondition). Closes the gap
   where ``refresh_dtssh_mesh`` correctly *detected* a self-unreachable host
   but nothing ever repaired it (copilot-extensions#3994).
3. ``refresh_dtssh_mesh`` -- re-discover live tunnel ids, re-emit this
   machine's SSH profile, and verify reachability to every declared peer
   (copilot-extensions#2684).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _peer_launch
from .self_update_types import CommandResult, StepResult, default_command_runner, shutil_which

WATCHDOG_START_TIMEOUT_SECONDS = 40


@dataclass(frozen=True)
class DtsshConfig:
    config_path: Path
    install_root: Path
    launcher_path: Path
    alias: str
    port: int
    tunnel: str | None = None
    user: str | None = None


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
    _legacy = "~/.agent-machines"  # marketplace-isolation: allow legacy-compatibility
    own_root = Path(
        os.environ.get("AGENT_RT_ROOT", _legacy)
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


def _parse_restore_host_json(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _restore_host_detail(payload: dict[str, Any]) -> str | None:
    for key in ("error", "blocked"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    stdout = payload.get("stdout")
    if isinstance(stdout, str) and stdout.strip():
        # restore-host's own status text already includes the discriminating
        # WARNING (e.g. "sshd NOT serving on :PORT -- no SSH banner"); surface
        # its last non-empty line rather than a generic message.
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return None


def ensure_dtssh_host_healthy(
    *, runner: Callable[..., CommandResult] = default_command_runner
) -> StepResult:
    """Detect and repair this machine's own dtssh host.

    Resolves this machine's own dtssh alias/port from the local
    ``dispatch-companion.json`` (the same source ``ensure_watchdog`` uses),
    previews health with ``agent-ssh restore-host --json``, and applies only
    when unhealthy -- the automated counterpart of what an operator runs by
    hand today (the ``performing-machine-maintenance`` skill's target-side
    drain: preview through the owning system, apply only through the normal
    owner, verify the stated postcondition).

    This closes the gap where ``refresh_dtssh_mesh`` correctly *detects* a
    self-unreachable host (a watchdog process can stay alive on an ancient,
    wedged snapshot indefinitely -- ``ensure_watchdog`` only checks that a
    launcher process is running, never that it is current or actually
    serving) but never repairs it (copilot-extensions#3994).

    Missing ``agent-ssh`` or no dtssh companion config is reported as
    ``skipped``, not an error: not every machine hosts a dtssh instance.
    """
    prefix = _agent_ssh_argv_prefix()
    if prefix is None:
        return StepResult(
            "dtssh-host-health", "skipped", "agent-ssh is not installed on this machine"
        )
    try:
        config = load_dtssh_config()
    except RuntimeError as exc:
        return StepResult("dtssh-host-health", "skipped", str(exc))

    base_argv = [
        *prefix,
        "restore-host",
        "--transport",
        "dtssh",
        "--alias",
        config.alias,
        "--port",
        str(config.port),
    ]
    status_result = runner([*base_argv, "--json"], timeout=180)
    status_payload = _parse_restore_host_json(status_result.stdout)
    if status_payload is None:
        return StepResult(
            "dtssh-host-health",
            "error",
            status_result.output or "restore-host status check failed",
            command=status_result.argv,
        )
    if status_payload.get("healthy") is True:
        return StepResult(
            "dtssh-host-health",
            "ok",
            _restore_host_detail(status_payload) or "dtssh host is healthy",
            command=status_result.argv,
        )
    if not status_payload.get("would_change"):
        # Unhealthy but restore-host itself does not believe an apply would
        # change anything (e.g. blocked on Dev Tunnel authentication) -- an
        # unattended apply would just repeat the same no-op. Report and let
        # the existing failure-propagation path (RunResult -> status.json ->
        # exit code) surface it instead of looping.
        return StepResult(
            "dtssh-host-health",
            "error",
            _restore_host_detail(status_payload) or "dtssh host is unhealthy",
            command=status_result.argv,
        )
    apply_result = runner([*base_argv, "--apply", "--json"], timeout=300)
    apply_payload = _parse_restore_host_json(apply_result.stdout)
    if apply_payload is None:
        return StepResult(
            "dtssh-host-health",
            "error",
            apply_result.output or "restore-host apply failed",
            command=apply_result.argv,
        )
    if apply_payload.get("healthy") is True:
        return StepResult(
            "dtssh-host-health",
            "changed",
            _restore_host_detail(apply_payload) or "repaired the dtssh host",
            command=apply_result.argv,
        )
    if apply_payload.get("verification_required") is True:
        # SSH-driven remote apply (copilot-extensions#1977): the repair was
        # launched to survive this session's own exit but is not yet
        # verified. Not a failure -- the next watchdog cycle re-checks health.
        detail = (
            _restore_host_detail(apply_payload)
            or "dtssh host repair launched; verification pending"
        )
        return StepResult(
            "dtssh-host-health",
            "changed",
            detail,
            command=apply_result.argv,
        )
    return StepResult(
        "dtssh-host-health",
        "error",
        _restore_host_detail(apply_payload) or "dtssh host remains unhealthy after apply",
        command=apply_result.argv,
    )
