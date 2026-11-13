"""Agent Bridge ownership seam for the persistent SSH carrier endpoint."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any

from ssh_manager import (
    CarrierLease,
    ConfigSource,
    ConnectionManager,
    SSHProfileSource,
    StdioCarrierServer,
    get_default_manager,
)


def build_remote_carrier_command(remote_platform: str, *, project: str | None = None) -> str:
    """Build the far-side ``agent-bridge carrier --stdio`` command.

    The installed binstub is addressed explicitly because non-interactive SSH
    sessions do not reliably inherit the user's local-bin PATH. Windows invokes
    the command shim through ``cmd.exe`` so the carrier's binary framed stdio is
    not decoded and re-encoded by a PowerShell native-command pipeline.
    """
    platform = remote_platform.strip().lower()
    if project is not None and (
        not isinstance(project, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", project) is None
    ):
        raise ValueError("project must name one registered repository command")
    command = project if project is not None else "agent-bridge"
    arguments = "bridge carrier --stdio" if project is not None else "carrier --stdio"
    if platform in {"windows", "win", "pwsh", "powershell"}:
        return (
            f'cmd.exe /d /s /c ""%USERPROFILE%\\.local\\bin\\{command}.cmd" '
            f'{arguments}"'
        )
    _posix_carrier = (
        f'"$HOME/.local/bin/{command}" '  # marketplace-isolation: allow remote-management
        f'{arguments}'
    )
    if platform in {"linux", "wsl", "posix", "sh", "bash"}:
        return _posix_carrier
    raise ValueError(f"unsupported remote carrier platform: {remote_platform!r}")


async def acquire_remote_carrier(
    host: str,
    remote_platform: str,
    *,
    config_source: ConfigSource | None = None,
    manager: ConnectionManager | None = None,
    port_forwards: list[str] | None = None,
    project: str | None = None,
    **options: Any,
) -> CarrierLease:
    """Acquire Agent Bridge's shared carrier for one normalized SSH identity."""
    owner = manager or get_default_manager()
    source = config_source or SSHProfileSource(host_alias=host)
    command = build_remote_carrier_command(remote_platform, project=project)
    await owner.ensure_connected(
        host,
        source,
        port_forwards=port_forwards,
        preserve_existing_forwards=port_forwards is None,
    )
    return await owner.acquire_carrier(
        host,
        command,
        **options,
    )


def _binary_stdio() -> tuple[object, object]:
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    return sys.stdin.buffer, sys.stdout.buffer


async def run_stdio_carrier() -> None:
    """Run the bounded carrier protocol until the SSH parent closes stdin."""
    from .remote_operations import CarrierRequestRouter

    reader, writer = _binary_stdio()
    await StdioCarrierServer(
        reader,
        writer,
        handler=CarrierRequestRouter(),
    ).run()


def cmd_carrier_stdio() -> None:
    """Synchronous CLI adapter."""
    asyncio.run(run_stdio_carrier())
