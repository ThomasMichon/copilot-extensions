"""Health monitoring and auto-reconnect for SSH connections."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from .config_sources import ConfigSource
from .manager import ConnectionManager, _creation_flags

log = logging.getLogger("ssh-manager")


@dataclass
class HealthStatus:
    """Result of a health check on an SSH connection."""

    ok: bool
    reason: Literal[
        "ok",
        "stale_socket",
        "process_dead",
        "check_failed",
        "not_connected",
        "not_multiplexed",
        "unknown",
    ]
    stderr: str | None = None

    @property
    def needs_reconnect(self) -> bool:
        return self.reason in ("stale_socket", "process_dead", "check_failed")


async def check_health(manager: ConnectionManager, host: str) -> HealthStatus:
    """Check the health of an SSH connection.

    Uses ``ssh -O check`` for ControlMaster connections. For direct-mode
    connections, checks if the host entry exists (no persistent process
    to check).
    """
    connections = {c.host: c for c in manager.list_connections()}

    if host not in connections:
        return HealthStatus(ok=False, reason="not_connected")

    info = connections[host]

    if not info.multiplexed:
        # Direct mode has no persistent master to check
        return HealthStatus(ok=True, reason="not_multiplexed")

    # Check if master process is still alive
    if info.master_process and info.master_process.returncode is not None:
        return HealthStatus(
            ok=False,
            reason="process_dead",
            stderr=f"Master process exited with code {info.master_process.returncode}",
        )

    # Check if socket still exists
    if not info.socket_path.exists():
        return HealthStatus(ok=False, reason="stale_socket")

    # Use ssh -O check to verify the ControlMaster is responsive
    args = ["ssh"]
    if info.config.config_file:
        args.extend(["-F", info.config.config_file])
    args.extend([
        "-o", f"ControlPath={info.socket_path}",
        "-O", "check",
        info.config.ssh_target,
    ])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        stderr = stderr_bytes.decode(errors="replace").rstrip()

        if proc.returncode == 0:
            return HealthStatus(ok=True, reason="ok")
        return HealthStatus(ok=False, reason="check_failed", stderr=stderr)

    except TimeoutError:
        return HealthStatus(
            ok=False, reason="check_failed", stderr="ssh -O check timed out"
        )
    except OSError as e:
        return HealthStatus(ok=False, reason="unknown", stderr=str(e))


async def ensure_healthy(
    manager: ConnectionManager,
    host: str,
    config_source: ConfigSource,
    port_forwards: list[str] | None = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
) -> HealthStatus:
    """Check health and reconnect if needed, with exponential backoff.

    Returns the final health status after any reconnection attempts.
    """
    status = await check_health(manager, host)
    if status.ok:
        return status

    if not status.needs_reconnect:
        return status

    for attempt in range(max_retries):
        log.info(
            "Reconnecting to %s (attempt %d/%d, reason: %s)",
            host, attempt + 1, max_retries, status.reason,
        )

        # Disconnect stale connection
        await manager.disconnect(host)

        # Refresh config before reconnecting
        config_source.refresh()

        # Wait with exponential backoff
        if attempt > 0:
            delay = backoff_base * (2 ** (attempt - 1))
            await asyncio.sleep(delay)

        try:
            await manager.ensure_connected(host, config_source, port_forwards)
            status = await check_health(manager, host)
            if status.ok:
                log.info("Reconnected to %s successfully", host)
                return status
        except (ConnectionError, OSError) as e:
            log.warning("Reconnect attempt %d failed for %s: %s", attempt + 1, host, e)
            status = HealthStatus(ok=False, reason="check_failed", stderr=str(e))

    log.error("Failed to reconnect to %s after %d attempts", host, max_retries)
    return status
