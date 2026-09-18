"""ACP teardown with optional strict, retryable ownership."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .acp_client import AcpClient


async def shutdown_client(client: AcpClient, *, strict: bool = False) -> None:
    """Close independent resources, retaining failed handles in strict mode."""
    from .acp_client import (
        CancelElicitationResponse, RequestPermissionResponse,
        _terminate_process_tree, log,
    )

    if client._pending_permission_future and not client._pending_permission_future.done():
        client._pending_permission_future.set_result(
            RequestPermissionResponse(outcome={"outcome": "cancelled"})
        )
        client._pending_permission_future = None
    client._cancel_out_of_turn()
    for future in client._pending_elicitations.values():
        if not future.done():
            future.set_result(CancelElicitationResponse(action="cancel"))
    client._pending_elicitations.clear()
    client._pending_ask_user_meta.clear()
    errors: list[tuple[str, Exception]] = []

    async def close(label: str, operation: Awaitable[Any]) -> bool:
        try:
            await operation
            return True
        except Exception as exc:
            log.warning("ACP %s cleanup failed", label, exc_info=True)
            if strict:
                errors.append((label, exc))
            return False

    connection = client._connection
    if connection is not None:
        closed = await close("connection", connection.close())
        if (closed or not strict) and client._connection is connection:
            client._connection = None
    if client._host_mode:
        closer = client._host_closer
        if closer is not None:
            closed = await close("host transport", closer())
            if (closed or not strict) and client._host_closer is closer:
                client._host_closer = None
    else:
        process = client._process
        if process is not None and process.returncode is None:
            if strict:
                closed = await close("process", _terminate_process_tree(process))
                if closed and process.returncode is None:
                    errors.append(("process", RuntimeError("ACP process exit remains unconfirmed")))
            else:
                await _terminate_process_tree(process)
        if not strict or process is None or process.returncode is not None:
            client._process = None
    if errors:
        raise RuntimeError(
            "ACP shutdown incomplete; retry ownership retained for: "
            + ", ".join(label for label, _error in errors)
        ) from errors[0][1]
    client._background_tasks.clear()
