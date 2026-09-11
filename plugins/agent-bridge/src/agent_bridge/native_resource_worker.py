"""Bounded local provider invocation holding an OS lock across controller loss."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from agent_procutil import no_window_kwargs
from single_instance_lease import AlreadyRunningError, SingleInstance

from .native_resources import MAX_BYTES, bounded_json


async def run_provider(argv, payload):
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, **no_window_kwargs(),
    )
    async def communicate():
        process.stdin.write(bounded_json(payload))
        await process.stdin.drain()
        process.stdin.close()
        chunks, size = [], 0
        while chunk := await process.stdout.read(min(4096, MAX_BYTES + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("provider reply too large")
        await process.wait()
        if process.returncode:
            raise ValueError("provider failed")
        return json.loads(b"".join(chunks))
    try:
        return await asyncio.wait_for(communicate(), 90)
    finally:
        if not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            process.kill()
            await process.wait()


def main():
    packet = json.loads(sys.stdin.buffer.read(MAX_BYTES + 1))
    payload = packet["request"]
    directory = Path(payload["stateDir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lease = SingleInstance(directory, service="native-host-resource")
    try:
        with lease:
            reply = asyncio.run(run_provider(packet["argv"], payload))
        sys.stdout.buffer.write(bounded_json(reply))
        sys.stdout.buffer.flush()
    except AlreadyRunningError:
        print(json.dumps({"workerError": "resource_busy"}))
    except (ValueError, OSError, TimeoutError, asyncio.TimeoutError):
        print(json.dumps({"workerError": "resource_provider_failed"}))


if __name__ == "__main__":
    main()
