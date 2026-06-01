"""Transport -- spawn Copilot ACP agent processes (local + SSH)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("agent-bridge")


@dataclass
class SpawnTarget:
    """Where and how to spawn an agent process."""

    type: str = "local"  # "local" or "ssh"
    cwd: str | None = None
    host: str | None = None  # SSH alias (from machines.yaml)
    user: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None  # agent-worktrees project (binstub name)

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
        """Terminate the subprocess."""
        if self.alive:
            try:
                self.proc.terminate()
                with asyncio.timeout(5):
                    await self.proc.wait()
            except (TimeoutError, ProcessLookupError):
                self.proc.kill()


async def spawn_local(target: SpawnTarget) -> AgentProcess:
    """Spawn a Copilot ACP agent as a local subprocess.

    When a ``project`` is configured, launches via the project binstub
    (e.g. ``my-project --no-mux -- --acp --stdio``).  The binstub
    resolves the setup script, loads vault credentials, creates a
    worktree session, and execs copilot.  This keeps secrets in the
    subprocess environment without transmitting them through the bridge.

    Without ``project``, runs copilot directly (legacy behavior).
    """
    env = os.environ.copy()
    env.update(target.env)

    if target.project:
        args = [
            target.project, "--no-mux", "--no-update",
            "--", "--acp", "--stdio",
        ] + target.copilot_args
        log.info("Spawning local agent via binstub: %s", " ".join(args))
    else:
        if not target.cwd:
            raise ValueError("Local agent without 'project' requires 'cwd'")
        copilot = target.copilot_path or _find_copilot()
        args = [copilot, "--acp", "--stdio"] + target.copilot_args
        log.info("Spawning local agent: %s (cwd=%s)", " ".join(args), target.cwd)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=target.cwd or None,
        env=env,
    )

    return AgentProcess(proc, target)


async def spawn_ssh(target: SpawnTarget) -> AgentProcess:
    """Spawn a Copilot ACP agent on a remote machine via SSH.

    Uses SSH config aliases from machines.yaml. The alias encodes host,
    port, key, and proxy settings via the local SSH config.

    Hardened for ACP protocol safety:
    - BatchMode=yes (no interactive password prompts)
    - -T (no PTY -- prevents MOTD/banner noise on stdout)
    - ServerAliveInterval for keepalive
    - ConnectTimeout for fast failure
    - All remote arguments shell-escaped
    """
    if not target.host:
        raise ValueError("SSH target requires a host (SSH alias)")

    copilot = target.copilot_path or "copilot"
    ssh_target = f"{target.user}@{target.host}" if target.user else target.host

    # Build the remote POSIX command
    if target.project:
        # Use the project binstub which handles setup scripts, vault
        # credentials, and copilot resolution.  Secrets stay on the remote
        # machine -- they never traverse the SSH channel back to the bridge.
        binstub_args = [
            target.project, "--no-mux", "--no-update",
            "--", "--acp", "--stdio",
        ]
        if target.copilot_args:
            binstub_args.extend(target.copilot_args)
        remote_cmd = " ".join(shlex.quote(a) for a in binstub_args)
    else:
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
        remote_cmd = " && ".join(parts)

    ssh_args = [
        "ssh",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",
        "-T",
        ssh_target,
        remote_cmd,
    ]

    log.info("Spawning SSH agent: %s", " ".join(ssh_args))

    proc = await asyncio.create_subprocess_exec(
        *ssh_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    return AgentProcess(proc, target)


async def spawn(target: SpawnTarget) -> AgentProcess:
    """Spawn an ACP agent process (local or SSH)."""
    if target.type == "ssh":
        return await spawn_ssh(target)
    return await spawn_local(target)


def _find_copilot() -> str:
    """Find the copilot CLI binary."""
    # Check environment override
    path = os.environ.get("COPILOT_PATH")
    if path:
        return path

    # Default to "copilot" on PATH
    return "copilot"
