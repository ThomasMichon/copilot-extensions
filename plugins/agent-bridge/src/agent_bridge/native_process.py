"""Bounded cleanup of a retained, owned native-provider subprocess."""

from __future__ import annotations

import asyncio
import logging

from .acp_client import _terminate_process_tree
from .native_store import NativeError

GRACE_SECONDS = 30.0
TERMINATE_SECONDS = 15.0
EXIT_SECONDS = 5.0
DRAIN_SECONDS = 5.0


async def close_owned_process(process, *, grace: float | None = None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                logging.getLogger("agent-bridge").debug("Native provider input pipe was already disconnected")
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), GRACE_SECONDS if grace is None else grace)
            except (TimeoutError, asyncio.TimeoutError):
                # Never recover kill authority from a stored PID or an exited
                # leader. This is the subprocess object this controller owns.
                if process.returncode is None:
                    await asyncio.wait_for(_terminate_process_tree(process), TERMINATE_SECONDS)
                await asyncio.wait_for(process.wait(), EXIT_SECONDS)
        if process.returncode is None:
            raise NativeError("retirement_unconfirmed", "Owned local transport exit is unconfirmed", 503)
    except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
        raise NativeError(
            "retirement_unconfirmed", "Owned local transport cleanup could not be confirmed; ownership retained", 503,
        ) from exc
