"""Captured owner launches keep uncertain work alive instead of killing it."""

from __future__ import annotations

import asyncio
import base64
import logging
import shlex
from typing import Any

from agent_procutil import no_window_kwargs

from .session_lifecycle_cli import (
    _agent_worktrees_environment,
    _agent_worktrees_launch_prefix,
)

log = logging.getLogger("agent-bridge")
_pending: set[asyncio.Task[tuple[bytes, bytes]]] = set()


class OwnerOperationUnconfirmed(RuntimeError):
    """The original launch is retained; its outcome is not yet known."""


class OwnerOperationRefused(RuntimeError):
    """The owner completed with an explicit refusal."""


def owner_ssh_command(project: str, args: list[str], shell: str | None) -> str:
    parts = [project, *args]
    if shell in {"pwsh", "powershell", "cmd"}:
        script = "& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        interpreter = "pwsh" if shell == "pwsh" else "powershell"
        return f"{interpreter} -NoProfile -NonInteractive -EncodedCommand {encoded}"
    return shlex.join(parts)


def _retain(task: asyncio.Task[tuple[bytes, bytes]]) -> None:
    _pending.add(task)

    def finished(result: asyncio.Task[tuple[bytes, bytes]]) -> None:
        _pending.discard(result)
        if result.cancelled():
            log.warning("Unconfirmed owner-operation capture was cancelled")
        elif result.exception() is not None:
            log.error("Unconfirmed owner operation failed: %s", result.exception())
        else:
            _stdout, stderr = result.result()
            if stderr:
                log.warning("Unconfirmed owner operation completed: %s", stderr.decode(errors="replace")[:500])

    task.add_done_callback(finished)


async def run_owner_operation(
    owner: str, config: Any, resolver: Any, args: list[str], *, timeout: float = 180,
) -> str:
    """Keep launch ownership and the SSH profile's connection identity intact."""
    target = resolver.resolve(owner)
    if target.type == "local":
        prefix = _agent_worktrees_launch_prefix()
        if not prefix:
            raise RuntimeError("worktree owner capability is unavailable")
        argv = [*prefix, "--project", config.project, *args]
    elif target.type == "ssh" and target.host:
        remote = owner_ssh_command(config.project, args, target.ssh_shell)
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-T", target.host, remote]
    else:
        raise ValueError("worktree owner has no local or SSH operation transport")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_agent_worktrees_environment(), **no_window_kwargs(),
    )
    capture = asyncio.create_task(proc.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(capture), timeout)
    except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError) as exc:
        _retain(capture)
        log.warning("Owner operation remains unconfirmed; preserving its process and native identity")
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise OwnerOperationUnconfirmed("Owner launch is still pending; its process was preserved") from exc
    if proc.returncode:
        log.error("Owner operation refused (%s): %s", proc.returncode, stderr.decode(errors="replace")[:500])
        raise OwnerOperationRefused(stderr.decode(errors="replace").strip() or f"Owner exited {proc.returncode}")
    return stdout.decode("utf-8")
