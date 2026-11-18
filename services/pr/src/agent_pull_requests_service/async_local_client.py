"""Finite asynchronous Unix exchanges for explicitly bound repository consumers."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
from pathlib import Path
import re
import socket
import struct
import sys
import time
from typing import Any

from agent_pull_requests.repository_contract import RepositoryIdentity

from .bound_local_client import BoundRepositoryClient
from .local_client import (
    MAX_BYTES, LocalClientError, LocalDeadlineExpired, _invalid_constant, _unique,
)

OPERATIONS = frozenset({
    "query", "next-actions", "subscribe", "inspect", "events", "acknowledge", "cancel",
})


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LocalDeadlineExpired("finite polling window elapsed; original IDs retained")
    return remaining


def _freeze_request(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    if type(payload) is not dict or len(payload) > 8:
        raise LocalClientError("bounded scalar local request required")
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if type(key) is not str or len(key) > 64:
            raise LocalClientError("bounded scalar local request required")
        if type(value) is str:
            valid = len(value) <= 512
        elif type(value) is int:
            valid = value.bit_length() <= 63
        elif type(value) is float:
            valid = math.isfinite(value)
        else:
            valid = value is None or type(value) is bool
        if not valid:
            raise LocalClientError("bounded scalar local request required")
        result[key] = value
    return result


async def _exchange(
    path: Path, target: str, method: str, operation: str, payload: dict[str, Any] | None,
    *, deadline: float,
) -> tuple[int, dict[str, Any]]:
    if sys.platform != "linux":
        raise LocalClientError("Linux local transport required")
    _remaining(deadline)
    reader, writer = await asyncio.open_unix_connection(str(path), limit=8192)
    try:
        peer = writer.get_extra_info("socket")
        if peer is None or struct.unpack(
            "iII", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII")),
        )[1] != os.getuid():
            raise LocalClientError("local server OS identity differs")
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else b""
        if len(body) > 32_768:
            raise LocalClientError("local request exceeds its byte budget")
        _remaining(deadline)
        writer.write((
            f"{method} /v1/repositories/{target}/{operation} HTTP/1.1\r\n"
            "Host: localhost\r\nConnection: close\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("ascii") + body)
        await writer.drain()
        header = await reader.readuntil(b"\r\n\r\n")
        if len(header) > 8192:
            raise LocalClientError("local response headers exceed their byte budget")
        lines = header[:-4].split(b"\r\n")
        status = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", lines[0])
        if status is None:
            raise LocalClientError("invalid local HTTP status")
        headers: dict[bytes, bytes] = {}
        for line in lines[1:]:
            key, separator, value = line.partition(b":")
            key = key.lower()
            if not separator or not re.fullmatch(rb"[a-z0-9-]+", key) or key in headers:
                raise LocalClientError("invalid or duplicate local HTTP header")
            headers[key] = value.strip()
        length = headers.get(b"content-length", b"")
        if b"transfer-encoding" in headers or re.fullmatch(rb"[0-9]{1,10}", length) is None:
            raise LocalClientError("bounded local HTTP content length required")
        size = int(length)
        if size > MAX_BYTES:
            raise LocalClientError("local response exceeds its byte budget")
        raw = await reader.readexactly(size)
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
        if not isinstance(value, dict):
            raise LocalClientError("invalid local response")
        return int(status[1]), value
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1)
        except (OSError, asyncio.TimeoutError):
            pass


class AsyncBoundRepositoryClient:
    """One bounded in-flight request, without executors, retries or activation.

    Cancellation closes the owned transport, not a possibly accepted service
    operation. The caller retains its original request and reconciles it.
    """

    def __init__(
        self, path: Path, target: str, *, identity: RepositoryIdentity, consumer: str,
        operation_seconds: float = 30,
    ) -> None:
        if (
            type(operation_seconds) not in (int, float)
            or not math.isfinite(operation_seconds) or not 0 < operation_seconds <= 30
        ):
            raise LocalClientError("finite local operation budget must be at most 30 seconds")
        self._selection = BoundRepositoryClient(path, target, identity=identity, consumer=consumer)
        self._seconds = operation_seconds
        self.deadline: float | None = None
        self._busy = False
        self._expires_at: float | None = None

    async def _http(
        self, method: str, operation: str, payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected = self._selection
        selected._check_transport()
        assert self._expires_at is not None
        status, value = await _exchange(
            selected.path, selected.target, method, operation, payload, deadline=self._expires_at,
        )
        _remaining(self._expires_at)
        selected._check_transport()
        if status != 200:
            raise LocalClientError(str(value.get("error", "local request unavailable")))
        if value.get("protocol") != selected.protocol or value.get("namespace") != selected.namespace:
            raise LocalClientError("local namespace/contract differs")
        return value

    async def _operation(
        self, operation: str | None, payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        selected = self._selection
        selected._check_transport()
        if operation is None or selected.binding is None:
            capabilities = await self._http("GET", "capabilities")
            selected._accept_expected_selection(capabilities)
            if operation is None:
                return copy.deepcopy(capabilities)
        assert operation is not None and payload is not None and selected.binding is not None
        if operation == "next-actions" and not selected.reviewer_actions:
            raise LocalClientError("selected local reviewer-action profile unsupported")
        value = await self._http("POST", operation, selected._envelope(payload))
        result = value.get("result")
        if value.get("repository_key") != selected.binding["repository_key"] or not isinstance(result, dict):
            raise LocalClientError("local binding/result differs")
        return result

    async def _run(
        self, operation: str | None, payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self._busy:
            raise LocalClientError("local client already has an in-flight operation")
        deadline = self.deadline
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise LocalClientError("invalid original local deadline")
        expires_at = time.monotonic() + self._seconds
        if deadline is not None:
            expires_at = min(expires_at, deadline)
        _remaining(expires_at)
        # Freeze caller-owned intent before yielding to negotiation or the socket.
        original = _freeze_request(payload)
        remaining = _remaining(expires_at)
        self._busy = True
        self._expires_at = expires_at
        try:
            return await asyncio.wait_for(self._operation(operation, original), timeout=remaining)
        except asyncio.TimeoutError:
            raise LocalDeadlineExpired("local operation deadline elapsed; original IDs retained") from None
        except (OSError, UnicodeError, json.JSONDecodeError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            raise LocalClientError("local service unavailable; retain original request/registration IDs") from None
        finally:
            self._busy = False
            self._expires_at = None

    async def negotiate(self) -> dict[str, Any]:
        """Check executing repository/consumer support without a subscription."""
        return await self._run(None, None)

    async def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Exchange one cache operation; never retry an uncertain accepted effect."""
        if (
            type(operation) is not str or len(operation) > 32
            or operation not in OPERATIONS or not isinstance(payload, dict)
        ):
            raise LocalClientError("unsupported local request")
        return await self._run(operation, payload)
