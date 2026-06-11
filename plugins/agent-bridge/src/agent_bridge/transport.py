"""Transport -- spawn Copilot ACP agent processes (local + SSH).

SSH connections are managed by the shared ssh-manager library, which
provides ControlMaster multiplexing on Unix and direct SSH fallback on
Windows. Multiple ACP sessions to the same host share a single master
connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from ssh_manager import SSHProfileSource, get_default_manager

log = logging.getLogger("agent-bridge")


def _check_port_alive(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Check if a local TCP port is listening."""
    import socket as _socket

    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, _socket.timeout, OSError):
        return False


def _creation_flags() -> int:
    """Return subprocess creation flags for the current platform.

    On Windows, ``CREATE_NO_WINDOW`` prevents console allocation failures
    (STATUS_DLL_INIT_FAILED / 0xC0000142) when spawning console subsystem
    executables from a headless background service like agent-bridge.
    """
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


@dataclass
class SpawnTarget:
    """Where and how to spawn an agent process."""

    type: str = "local"  # "local", "ssh", or "command"
    cwd: str | None = None
    host: str | None = None  # SSH alias (from machines.yaml)
    user: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None  # agent-worktrees project (binstub name)
    ssh_shell: str | None = None  # remote shell (e.g. "pwsh", "bash")
    worktree_id: str | None = None  # resume a specific worktree
    spawn_command: list[str] | None = None  # raw command for provider agents
    auth_hooks: list[dict] = field(default_factory=list)  # serializable auth hook dicts

    def to_json(self) -> str:
        """Serialize for DB persistence."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> SpawnTarget:
        """Deserialize from DB."""
        data: dict[str, Any] = json.loads(raw)
        return cls(**data)


class AgentProcess:
    """Wraps an asyncio subprocess running copilot --acp --stdio."""

    def __init__(self, proc: asyncio.subprocess.Process, target: SpawnTarget) -> None:
        self.proc = proc
        self.target = target

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def write(self, data: bytes) -> None:
        """Write data to the process stdin."""
        if self.proc.stdin:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def readline(self) -> bytes:
        """Read a line from the process stdout."""
        if self.proc.stdout:
            return await self.proc.stdout.readline()
        return b""

    async def kill(self) -> None:
        """Terminate the subprocess and its entire child tree.

        ``proc.terminate()`` only reaps the direct child -- on Windows that is
        the ``cmd.exe`` batch wrapper, which orphans the ``pwsh -> copilot`` (or
        ``python -> ssh``) tree beneath it, leaving processes that hold the
        worktree directory open. Kill the whole tree instead.
        """
        if not self.alive:
            return
        pid = self.proc.pid
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                async with asyncio.timeout(5):
                    await killer.wait()
            except (TimeoutError, OSError, ProcessLookupError):
                pass
        else:
            # POSIX: the agent spawns use start_new_session, so the child is a
            # process-group leader -- signal the whole group.
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.proc.terminate()
                except ProcessLookupError:
                    pass
        # Reap the direct child handle.
        try:
            async with asyncio.timeout(5):
                await self.proc.wait()
        except (TimeoutError, ProcessLookupError):
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


def _wrap_batch_for_windows(
    args: list[str], env: dict[str, str],
) -> list[str]:
    """Wrap .cmd/.bat executables with cmd.exe on Windows.

    ``asyncio.create_subprocess_exec`` uses ``CreateProcess`` which
    cannot execute batch files directly.  When the resolved executable
    ends with ``.cmd`` or ``.bat``, we prepend ``cmd.exe /d /s /c`` so
    that ``CreateProcess`` receives a real PE executable.

    On non-Windows platforms this is a no-op.
    """
    if sys.platform != "win32":
        return args

    exe = args[0]
    resolved = shutil.which(exe, path=env.get("PATH"))
    target_path = resolved or exe

    if target_path.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        args = [comspec, "/d", "/s", "/c", target_path, *args[1:]]
        log.debug("Wrapped batch file for Windows: %s", " ".join(args))

    elif resolved:
        # Use the fully resolved path even for non-batch executables
        args = [resolved, *args[1:]]

    return args


async def _resolve_worktree(
    target: SpawnTarget, env: dict[str, str],
) -> dict:
    """Run ``agent-worktrees resolve --json`` to get a launch plan.

    Calls the agent-worktrees Python module directly (bypassing the
    .cmd binstub and cmd.exe) to avoid console allocation issues when
    running from a headless background service on Windows.

    Returns the parsed JSON plan dict.
    """
    # Replicate the binstub's Python + PYTHONPATH setup
    home = os.path.expanduser("~")
    aw_venv = os.path.join(home, ".agent-worktrees", ".venv")
    aw_lib = os.path.join(home, ".agent-worktrees", "lib")

    if sys.platform == "win32":
        python = os.path.join(aw_venv, "Scripts", "python.exe")
    else:
        python = os.path.join(aw_venv, "bin", "python")

    if not os.path.exists(python):
        raise RuntimeError(
            f"agent-worktrees venv not found at {python}"
        )

    # Set PYTHONPATH so agent_worktrees module is importable,
    # and WORKTREE_PROJECT so it resolves the right project config.
    # Clear VIRTUAL_ENV/PYTHONHOME to avoid the bridge's own venv
    # polluting the agent-worktrees subprocess (they may use different
    # Python versions).
    env = dict(env)
    env["PYTHONPATH"] = aw_lib
    env["PYTHONUTF8"] = "1"
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    if target.project:
        env["WORKTREE_PROJECT"] = target.project

    resolve_args = [python, "-m", "agent_worktrees", "resolve", "--json", "--no-resume"]
    if target.worktree_id:
        resolve_args.extend(["--worktree-id", target.worktree_id])
    else:
        resolve_args.append("--new")

    log.info("Resolving worktree: %s", " ".join(resolve_args))

    proc = await asyncio.create_subprocess_exec(
        *resolve_args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        creationflags=_creation_flags(),
    )
    stdout, stderr = await proc.communicate()

    if stderr:
        for line in stderr.decode(errors="replace").strip().splitlines():
            log.debug("resolve stderr: %s", line)

    if proc.returncode != 0:
        err_text = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Worktree resolve failed (exit {proc.returncode}): {err_text}"
        )

    try:
        plan = json.loads(stdout.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"Worktree resolve returned invalid JSON: {exc}"
        ) from exc

    return plan


async def spawn_local(target: SpawnTarget) -> AgentProcess:
    """Spawn a Copilot ACP agent as a local subprocess.

    When a ``project`` is configured, uses a two-step flow:

    1. **Resolve** -- calls ``<project> resolve --json --new`` (or
       ``--worktree-id <id>``) to create/resume a worktree and get a
       JSON launch plan containing the copilot command, work directory,
       and environment variables.
    2. **Exec** -- launches copilot directly using the plan, with
       ``--acp --stdio`` appended.  This gives agent-bridge clean
       ownership of copilot's stdin/stdout for ACP framing, without
       any launcher or binstub output in the stdio stream.

    The binstub's ``resolve`` subcommand routes directly to the
    agent-worktrees resolve handler -- it does NOT go through
    launch-session scripts, so there is no update noise, picker
    output, or Write-Host pollution.

    Without ``project``, runs copilot directly (legacy behavior).
    """
    env = os.environ.copy()
    # Strip bridge's venv vars so child processes use their own Python
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.update(target.env)

    if target.project:
        plan = await _resolve_worktree(target, env)

        launch = plan.get("launch", plan)
        work_dir = launch.get("work_dir")
        cmd = launch.get("cmd", [])
        plan_env = launch.get("env", {})
        worktree_id = launch.get("worktree_id")

        if not cmd:
            raise RuntimeError("Worktree resolve returned empty cmd")

        # Store resolved values back into target for DB persistence
        if worktree_id and not target.worktree_id:
            target.worktree_id = worktree_id
        if work_dir and not target.cwd:
            target.cwd = work_dir

        # Merge plan environment into the process env
        env.update(plan_env)

        # Append ACP protocol args + any extra copilot args
        args = cmd + ["--acp", "--stdio"] + target.copilot_args
        log.info(
            "Spawning copilot from worktree plan: %s (cwd=%s, worktree=%s)",
            " ".join(args), work_dir, worktree_id,
        )
    else:
        if not target.cwd:
            raise ValueError("Local agent without 'project' requires 'cwd'")
        copilot = target.copilot_path or _find_copilot()
        args = [copilot, "--acp", "--stdio"] + target.copilot_args
        work_dir = target.cwd
        log.info("Spawning local agent: %s (cwd=%s)", " ".join(args), work_dir)

    args = _wrap_batch_for_windows(args, env)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or None,
        env=env,
        creationflags=_creation_flags(),
        start_new_session=(sys.platform != "win32"),
    )

    return AgentProcess(proc, target)


def _build_remote_cmd(target: SpawnTarget) -> str:
    """Build the POSIX remote command string for SSH execution.

    Two modes:
    - With ``project``: uses the project binstub (handles setup scripts,
      vault credentials, copilot resolution on the remote side).
    - Without ``project``: cd + export + exec copilot (legacy).
    """
    copilot = target.copilot_path or "copilot"

    if target.project:
        if target.worktree_id:
            # Session roll: resume existing worktree, skip Copilot session
            # resume (bridge manages ACP sessions independently)
            binstub_args = [
                target.project, "--worktree-id", target.worktree_id,
                "--no-mux", "--no-update", "--no-resume",
                "--", "--acp", "--stdio",
            ]
        else:
            binstub_args = [
                target.project, "--new", "--no-mux", "--no-update",
                "--", "--acp", "--stdio",
            ]
        if target.copilot_args:
            binstub_args.extend(target.copilot_args)
        binstub_cmd = " ".join(shlex.quote(a) for a in binstub_args)
        # Prepend env exports (e.g. auth hook vars) so they're available
        # to the binstub and all child processes in the SSH session
        if target.env:
            exports = " && ".join(
                f"export {k}={shlex.quote(v)}" for k, v in target.env.items()
            )
            return f"{exports} && {binstub_cmd}"
        return binstub_cmd

    if not target.cwd:
        raise ValueError("SSH agent without 'project' requires 'cwd'")
    parts = [f"cd {shlex.quote(target.cwd)}"]
    if target.env:
        for k, v in target.env.items():
            parts.append(f"export {k}={shlex.quote(v)}")
    copilot_cmd = f"exec {shlex.quote(copilot)} --acp --stdio"
    if target.copilot_args:
        copilot_cmd += " " + " ".join(shlex.quote(a) for a in target.copilot_args)
    parts.append(copilot_cmd)
    return " && ".join(parts)


async def spawn_ssh(target: SpawnTarget) -> AgentProcess:
    """Spawn a Copilot ACP agent on a remote machine via SSH.

    Uses ssh-manager's ConnectionManager for ControlMaster multiplexing.
    The manager maintains a persistent master connection per host, and
    subsequent ACP sessions multiplex over it (on Unix). On Windows,
    falls back to direct SSH (no multiplexing).

    Auth hooks from the machine topology are applied automatically:
    - Port forwards (-R) are passed to the master connection
    - Environment variables are injected into the remote command
    - Local service liveness is checked before connecting

    SSH hardening (BatchMode, -T, ConnectTimeout, ServerAliveInterval)
    is handled by ssh-manager's base args.
    """
    if not target.host:
        raise ValueError("SSH target requires a host (SSH alias)")

    # Resolve auth hooks into port forwards and env vars
    port_forwards: list[str] = []
    auth_env: dict[str, str] = {}
    for hook in target.auth_hooks:
        local_port = hook.get("local_port", 0)
        remote_port = hook.get("remote_port") or local_port
        hook_name = hook.get("name", "unknown")
        if local_port:
            if not _check_port_alive(local_port):
                log.warning(
                    "Auth hook '%s': local port %d is not listening -- "
                    "skipping port forward (auth may not work on remote)",
                    hook_name, local_port,
                )
            else:
                port_forwards.append(f"-R {remote_port}:127.0.0.1:{local_port}")
                log.info(
                    "Auth hook '%s': forwarding remote:%d -> local:%d",
                    hook_name, remote_port, local_port,
                )
        hook_env = hook.get("env", {})
        if hook_env:
            auth_env.update(hook_env)
            log.info(
                "Auth hook '%s': injecting env vars: %s",
                hook_name, list(hook_env.keys()),
            )

    # Merge auth env into target env (auth hooks have lowest precedence)
    if auth_env:
        merged = dict(auth_env)
        merged.update(target.env)
        target.env = merged

    manager = get_default_manager()
    source = SSHProfileSource(host_alias=target.host, user=target.user)

    try:
        await manager.ensure_connected(
            target.host, source, port_forwards=port_forwards or None,
        )
    except ConnectionError as exc:
        raise RuntimeError(
            f"Failed to establish SSH connection to {target.host}"
        ) from exc

    remote_cmd = _build_remote_cmd(target)
    log.info("Spawning SSH agent on %s: %s", target.host, remote_cmd)

    proc = await manager.open_stdio_channel(target.host, remote_cmd)
    return AgentProcess(proc, target)


async def spawn(target: SpawnTarget) -> AgentProcess:
    """Spawn an ACP agent process (local, SSH, or command)."""
    if target.type == "command" or target.spawn_command:
        return await spawn_raw(target)
    if target.type == "ssh":
        return await spawn_ssh(target)
    return await spawn_local(target)


async def spawn_raw(target: SpawnTarget) -> AgentProcess:
    """Spawn an ACP agent via a raw command.

    Used for provider agents that handle their own transport (e.g.
    agent-codespaces wraps SSH connection and copilot launch internally).
    The command is expected to speak ACP protocol on stdin/stdout.
    """
    if not target.spawn_command:
        raise ValueError("Command target requires spawn_command")

    env = os.environ.copy()
    # Strip bridge's venv vars so child processes use their own Python
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.update(target.env)

    args = _wrap_batch_for_windows(list(target.spawn_command), env)
    log.info("Spawning command agent: %s", " ".join(args))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        creationflags=_creation_flags(),
        start_new_session=(sys.platform != "win32"),
    )

    return AgentProcess(proc, target)


def _find_copilot() -> str:
    """Find the copilot CLI binary."""
    # Check environment override
    path = os.environ.get("COPILOT_PATH")
    if path:
        return path

    # Default to "copilot" on PATH
    return "copilot"


async def shutdown_ssh() -> None:
    """Disconnect all SSH master connections.

    Called during app shutdown, after ACP sessions are stopped.
    Safe to call even if no connections exist.
    """
    try:
        manager = get_default_manager()
        await manager.disconnect_all()
    except Exception:
        log.warning("Error during SSH connection shutdown", exc_info=True)
