"""Bounded, one-shot Host observations, never a controller attachment."""

from __future__ import annotations

import asyncio
import json
import math
import secrets
import time
from typing import Any, cast

from . import protocol as proto

MAX_BYTES = 4096
CONTRACT = "copilot-driver.host-inspection"


def encode(value: dict[str, Any]) -> bytes:
    wire = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(wire) > MAX_BYTES:
        raise ValueError("Host inspection exceeds its bound")
    return wire


def request(payload: bytes, credential: str) -> str | None:
    if len(payload) > MAX_BYTES:
        return None
    try:
        value = json.loads(payload)
        if (
            type(value) is not dict or value.get("contract") != CONTRACT or value.get("version") != 1
            or type(value.get("version")) is not int
            or type(value.get("credential")) is not str
            or not secrets.compare_digest(value["credential"].encode(), credential.encode())
            or type(value.get("nonce")) is not str or len(value["nonce"]) != 32
            or any(c not in "0123456789abcdef" for c in value["nonce"])
        ):
            return None
        return cast(str, value["nonce"])
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return None


def _validate(value: Any, nonce: str, child_pid: int) -> bool:
    if (
        type(value) is not dict or value.get("contract") != CONTRACT
        or value.get("version") != 1 or type(value.get("version")) is not int
    ):
        return False
    if value.get("nonce") != nonce:
        return False
    generation = value.get("host_generation")
    if (
        type(generation) is not str or len(generation) != 32
        or any(c not in "0123456789abcdef" for c in generation)
    ):
        return False
    for key in ("host_pid", "child_pid", "max_seq", "frontend_ack", "buffer_depth"):
        if type(value.get(key)) is not int or not 0 <= value[key] < 2**64:
            return False
    if not value["host_pid"] or (child_pid and value["child_pid"] != child_pid):
        return False
    if any(type(value.get(key)) is not bool for key in ("child_alive", "turn_in_flight")):
        return False
    if "last_child_frame_age_s" not in value:
        return False
    age = value["last_child_frame_age_s"]
    return age is None or (
        type(age) in (float, int) and math.isfinite(age) and age >= 0
    )


async def probe(
    host: str, port: int, credential: str, *, child_pid: int = 0, timeout: float = 1.0,
) -> dict[str, Any]:
    """Use only the existing route; never attach, refresh a forward, or retry."""
    started = time.monotonic()
    writer: asyncio.StreamWriter | None = None

    async def exchange() -> dict[str, Any]:
        nonlocal writer
        reader, writer = await asyncio.open_connection(host, port, limit=MAX_BYTES + 5)
        nonce = secrets.token_hex(16)
        await proto.write_message(writer, proto.MsgType.PING, encode({
            "contract": CONTRACT, "version": 1, "nonce": nonce, "credential": credential,
        }))
        reply = await proto.read_message(reader, max_bytes=MAX_BYTES + 1)
        if reply is None:
            return {"state": "unsupported", "reason": "no-inspection-response"}
        if reply[0] != proto.MsgType.PONG:
            return {"state": "invalid", "reason": "unexpected-inspection-response"}
        value = json.loads(reply[1])
        if (
            type(value) is dict and value.get("contract") == CONTRACT
            and type(value.get("version")) is int and value["version"] > 1
        ):
            return {"state": "unsupported", "reason": "unsupported-inspection-version"}
        if not _validate(value, nonce, child_pid):
            return {"state": "invalid", "reason": "invalid-inspection-snapshot"}
        # Unknown additive fields must not become an unbounded public projection.
        keys = (
            "host_pid", "host_generation", "child_pid", "child_alive", "turn_in_flight",
            "max_seq", "frontend_ack", "last_child_frame_age_s", "buffer_depth",
        )
        return {
            "state": "responsive", "rtt_s": time.monotonic() - started,
            "nonce": nonce, "snapshot": {key: value[key] for key in keys},
        }

    try:
        return await asyncio.wait_for(exchange(), timeout)
    except (TimeoutError, asyncio.TimeoutError):
        return {"state": "timed_out", "reason": "inspection-deadline"}
    except (proto.ProtocolError, ValueError, UnicodeError, OverflowError, RecursionError):
        return {"state": "invalid", "reason": "malformed-inspection-response"}
    except (OSError, ConnectionError):
        return {"state": "unavailable", "reason": "inspection-transport-unavailable"}
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), .1)
            except (OSError, asyncio.TimeoutError):
                writer.transport.abort()
