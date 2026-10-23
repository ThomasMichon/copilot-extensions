"""Incremental capture for an async process whose lifecycle remains caller-owned."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def communicate_progress(
    process: asyncio.subprocess.Process,
    *,
    input_bytes: bytes | None = None,
    on_output: Callable[[str, bytes], None],
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise ValueError("progress capture requires both captured output streams")
    if input_bytes is not None and process.stdin is None:
        raise ValueError("progress capture input requires a stdin pipe")

    async def collect(name: str, stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        while chunk := await stream.read(4096):
            on_output(name, chunk)
            chunks.append(chunk)
        return b"".join(chunks)

    async def write_input() -> None:
        writer = process.stdin
        if writer is None:
            return
        try:
            if input_bytes:
                writer.write(input_bytes)
                await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()

    tasks = [
        asyncio.create_task(collect("stdout", process.stdout)),
        asyncio.create_task(collect("stderr", process.stderr)),
        asyncio.create_task(write_input()),
    ]
    try:
        stdout, stderr, _ = await asyncio.gather(*tasks)
        await process.wait()
        return stdout, stderr
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
