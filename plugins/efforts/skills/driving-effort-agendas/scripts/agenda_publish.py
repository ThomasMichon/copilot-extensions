"""Optional bounded argv-only observation delivery after the SQLite commit."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
from typing import Any

from agenda import AgendaError, _text, decode
from agenda_store import delivery_record, encode, record_delivery

BODY_LIMIT = 32 * 1024
REPLY_LIMIT = 35 * 1024
STDERR_LIMIT = 32 * 1024


async def _invoke(argv: list[str], payload: bytes, *, timeout: float = 10) -> dict[str, Any]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=flags)
    assert process.stdin and process.stdout and process.stderr

    async def send() -> None:
        assert process.stdin
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()

    async def receive(stream: asyncio.StreamReader, limit: int, label: str) -> bytes:
        result = bytearray()
        while True:
            chunk = await stream.read(min(4096, limit + 1 - len(result)))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
            if len(result) > limit:
                raise AgendaError(f"publisher {label} exceeds {limit // 1024} KiB")

    tasks = [asyncio.create_task(send()),
             asyncio.create_task(receive(process.stdout, REPLY_LIMIT, "reply")),
             asyncio.create_task(receive(process.stderr, STDERR_LIMIT, "stderr")),
             asyncio.create_task(process.wait())]
    try:
        values = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
        stdout, stderr, code = values[1:]
        if code != 0:
            raise AgendaError(f"publisher exited {code}: {stderr.decode('utf-8', errors='replace')[:1024]}")
        reply = decode(stdout, "publisher reply")
        if reply.get("ok") is not True:
            error = reply.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(
                    error.get("message"), str):
                raise AgendaError("publisher must return ok:true or a structured error")
            _text(error["code"], "publisher error code")
            _text(error["message"], "publisher error message")
            return {"state": "pending", "error": error}
        return {"state": "delivered", "error": None, "reply": reply}
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=2)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def publish(home: Path, argv: list[str]) -> dict[str, Any]:
    revision, observation = delivery_record(home)
    if not argv:
        status: dict[str, Any] = {"state": "not-configured", "error": {
            "code": "not-configured", "message": "No explicit publisher argv was supplied."}}
    else:
        payload = (encode(observation) + "\n").encode()
        if observation.get("schema") == "efforts.agenda-undeliverable":
            status = {"state": "pending", "error": observation["error"]}
        elif len(payload) > BODY_LIMIT:
            status = {"state": "pending", "error": {
                "code": "observation-too-large", "message": "Observation exceeds 32 KiB; no argv invoked."}}
        elif any(not isinstance(token, str) or not token or "\x00" in token for token in argv):
            raise AgendaError("publisher argv must contain nonempty tokens without NUL")
        else:
            try:
                status = asyncio.run(_invoke(argv, payload))
            except (OSError, AgendaError, TimeoutError) as exc:
                status = {"state": "pending", "error": {
                    "code": "publisher-unavailable", "message": str(exc) or "Publisher timed out."}}
    return record_delivery(home, revision, status)
