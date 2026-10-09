"""Own the attestation channel and failed-start child until launch commits."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from ..preference_attestation import prepare_launch, read_frame, verify_frame


async def spawn_attested(
    argv: list[str], cwd: str | None, env: dict[str, str] | None, spawn: Any,
) -> tuple[Any, dict[str, Any]]:
    args, child_env, expected, reader, writer = prepare_launch(argv, env, cwd)
    child = None
    reader.settimeout(30)
    try:
        child = await spawn(args, cwd, child_env, pass_fds=(writer.fileno(),))
        writer.close()
        frame = await asyncio.to_thread(read_frame, reader)
        receipt = verify_frame(frame, expected, child.pid)
        reader.sendall(b"\x01")
        return child, receipt
    except BaseException:
        # Closing the channel refuses exec; reap this launch's own child only.
        try:
            reader.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        reader.close()
        if child is not None and child.returncode is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(child.wait(), 5)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        raise
    finally:
        writer.close()
        reader.close()
