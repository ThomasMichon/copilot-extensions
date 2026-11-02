"""Optional bounded argv-only observation delivery after the SQLite commit."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from agenda import AgendaError, _text, decode
from agenda_store import delivery_record, encode, record_delivery, safe_path

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


def publish(home: Path, argv: list[str], *, provider_python: str | None = None,
            provider_project: str | None = None) -> dict[str, Any]:
    home = safe_path(home, directory=True)
    revision, observation = delivery_record(home)
    if observation.get("schema") == "efforts.agenda-undeliverable":
        return record_delivery(home, revision, {"state": "pending", "error": observation["error"]})
    if observation.get("version") == 2:
        if argv or provider_python is None or provider_project is None:
            return record_delivery(home, revision, {"state": "pending", "error": {
                "code": "publisher-v2-not-configured",
                "message": "Observation-v2 requires explicit provider interpreter/project, not unnegotiated argv."}})
        source = str(Path(__file__).absolute().parent)
        bootstrap = ("import sys; sys.path.insert(0," + repr(source) + "); "
                     "from agenda_provider import main; raise SystemExit(main())")
        argv = [sys.executable, "-I", "-B", "-c", bootstrap,
                "--provider-python", provider_python, "--project", provider_project]
    elif provider_python is not None or provider_project is not None:
        raise AgendaError("provider-v2 configuration does not alter legacy publication")
    if not argv:
        status: dict[str, Any] = {"state": "not-configured", "error": {
            "code": "not-configured", "message": "No explicit publisher argv was supplied."}}
    else:
        payload = (encode(observation) + "\n").encode()
        if len(payload) > BODY_LIMIT:
            status = {"state": "pending", "error": {
                "code": "observation-too-large", "message": "Observation exceeds 32 KiB; no argv invoked."}}
        elif any(not isinstance(token, str) or not token or "\x00" in token for token in argv):
            raise AgendaError("publisher argv must contain nonempty tokens without NUL")
        else:
            try:
                status = asyncio.run(_invoke(argv, payload))
            except (OSError, AgendaError, asyncio.TimeoutError, TimeoutError) as exc:
                status = {"state": "pending", "error": {
                    "code": "publisher-unavailable", "message": str(exc) or "Publisher timed out."}}
    return record_delivery(home, revision, status)
