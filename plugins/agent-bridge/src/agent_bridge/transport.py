"""Transport -- spawn Copilot ACP agent processes.

Phase 1: local stdio only. SSH transport is Phase 2.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from dataclasses import dataclass, field

log = logging.getLogger("agent-bridge")


@dataclass
class SpawnTarget:
    """Where and how to spawn an agent process."""

    type: str = "local"  # "local" or "ssh" (Phase 2)
    cwd: str = "."
    host: str | None = None
    user: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


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

    Runs: copilot --acp --stdio [extra_args...] in the target directory.
    """
    copilot = target.copilot_path or _find_copilot()
    args = [copilot, "--acp", "--stdio"] + target.copilot_args

    env = os.environ.copy()
    env.update(target.env)

    log.info("Spawning local agent: %s (cwd=%s)", " ".join(args), target.cwd)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=target.cwd,
        env=env,
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
