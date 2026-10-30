"""Cooperative, generation-bound shutdown through the existing owner registry."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any
import uuid

CONTRACT = "agent-codespaces.owner-control"
VERSION = 1
_MAX_BYTES = 16384
log = logging.getLogger("agent-codespaces")


def _path() -> Path:
    from . import connection_owner as owner

    return owner.RUNTIME_DIR / "connection-owner.stop.json"


def _read() -> dict[str, Any]:
    try:
        with _path().open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            return {}
        value = json.loads(data)
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(value: dict[str, Any]) -> None:
    path = _path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _matches(value: dict[str, Any], pid: int, identity: str | None, host: str) -> bool:
    return bool(identity) and (
        value.get("contract") == CONTRACT and type(value.get("version")) is int
        and value["version"] == VERSION and type(value.get("pid")) is int
        and value["pid"] == pid and value.get("process_identity") == identity
        and value.get("host") == host and isinstance(value.get("request_id"), str)
        and bool(value["request_id"])
    )


def stop_requested() -> bool:
    """Consume only this process's request; caller holds the owner registry lock."""
    from . import connection_owner as owner

    value = _read()
    if not _matches(value, os.getpid(), owner.owner_process_identity(os.getpid()), owner._this_host()):
        return False
    if value.get("state") not in {"requested", "stopping"}:
        return False
    if value["state"] == "requested":
        _write({**value, "state": "stopping"})
    return True


def complete_owner_stop(succeeded: bool) -> None:
    """Acknowledge this generation only after its normal channel cleanup returns."""
    from . import connection_owner as owner

    try:
        with owner._owner_lock():
            value = _read()
            if _matches(value, os.getpid(), owner.owner_process_identity(os.getpid()), owner._this_host()):
                if value.get("state") in {"requested", "stopping"}:
                    _write({**value, "state": "stopped" if succeeded else "failed"})
    except (OSError, RuntimeError) as error:
        # No acknowledgement is preferable to replacing the primary cleanup
        # error or claiming success after an acknowledgement write failed.
        log.warning("Connection Owner shutdown acknowledgement unavailable: %s", error)


async def shutdown_owner(owner: Any) -> None:
    """Preserve normal cleanup errors and report their actual outcome."""
    from .owner_beacon import owner_native_call

    succeeded = False
    try:
        await owner.shutdown()
        succeeded = True
    finally:
        await owner_native_call(complete_owner_stop, succeeded)


def request_owner_stop(*, timeout: float = 30, poll: float = .1) -> dict[str, Any]:
    """Request and await cleanup of one proved owner, without signals or retargeting.

    Version 1 is advertised by the running beacon, not the installed package.
    A legacy/unknown owner is unsupported before any request is written. Timeout
    leaves the original request outstanding; it never falls back to termination.
    """
    from . import connection_owner as owner

    if timeout <= 0 or poll <= 0:
        raise ValueError("Owner stop timeout and poll must be positive")
    deadline = time.monotonic() + timeout
    with owner._owner_lock(timeout=min(timeout, 10)):
        live = owner.read_liveness()
        if live is None:
            if owner.LIVE_FILE.exists():
                raise RuntimeError("Connection Owner beacon is unreadable; shutdown identity is unknown")
            return {"status": "absent"}
        if (live.pid <= 0 or not live.process_identity or live.host != owner._this_host()
                or owner.owner_process_identity(live.pid) != live.process_identity):
            raise RuntimeError("Connection Owner shutdown requires its verified local process birth identity")
        if live.owner_control_version != VERSION:
            raise RuntimeError("Running Connection Owner does not support cooperative stop v1")
        request = _read()
        if not _matches(request, live.pid, live.process_identity, live.host):
            request = {
                "contract": CONTRACT, "version": VERSION, "request_id": uuid.uuid4().hex,
                "pid": live.pid, "process_identity": live.process_identity,
                "host": live.host, "state": "requested",
            }
            _write(request)
    while True:
        with owner._owner_lock(timeout=max(.001, min(10, deadline - time.monotonic()))):
            result = _read()
        if (result.get("request_id") != request["request_id"]
                or not _matches(result, live.pid, live.process_identity, live.host)):
            raise RuntimeError("Connection Owner stop request changed; refusing to target another generation")
        if result.get("state") == "stopped":
            return {"status": "stopped", "pid": live.pid, "process_identity": live.process_identity}
        if result.get("state") == "failed":
            raise RuntimeError("Connection Owner normal channel cleanup failed")
        if time.monotonic() >= deadline:
            raise TimeoutError("Connection Owner cleanup is not acknowledged; original stop request retained")
        time.sleep(min(poll, max(0, deadline - time.monotonic())))
