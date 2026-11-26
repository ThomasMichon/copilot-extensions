"""ACP teardown outcomes and opt-in native cancellation settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .acp_client import AcpClient


async def cancel_and_settle(client: AcpClient) -> None:
    if not client._prompt_in_flight:
        return
    if client._connection is None or not client.acp_session_id:
        raise RuntimeError("Native cancellation has no attached connection")
    async def settle() -> None:
        await client.cancel_prompt()
        async with client._prompt_lock:
            if client._prompt_in_flight or not client._prompt_complete or client._prompt_error is not None:
                raise RuntimeError("Native cancellation outcome is unconfirmed")

    await asyncio.wait_for(settle(), timeout=10.0)


async def shutdown_exact(
    client: AcpClient,
    terminate: Callable[[asyncio.subprocess.Process], Awaitable[None]],
) -> None:
    if client.has_active_background_tasks:
        raise RuntimeError("Background work became active during exact stop")
    if client._connection:
        await client._connection.close()
        client._connection = None
    if client.has_active_background_tasks:
        raise RuntimeError("Background work became active during exact stop")
    if client._host_mode:
        if client._host_closer is not None:
            await client._host_closer()
            client._host_closer = None
    else:
        proc = client._process
        if proc and proc.returncode is None:
            await terminate(proc)
            if proc.returncode is None:
                raise RuntimeError("Native process exit is unconfirmed")
        client._process = None
    if client.has_active_background_tasks:
        raise RuntimeError("Background work became active during exact stop")
    await client.shutdown()
