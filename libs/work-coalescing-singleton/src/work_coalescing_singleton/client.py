"""The caller side: dial an existing daemon, boot one on demand, or fall back.

Pure functions over a resolved ``(host, port, token)`` endpoint -- this module
never resolves rendezvous or boots a daemon itself; each consumer plugin
already owns that (its own session-state / installation-cell layout).
"""

from __future__ import annotations

import json
import secrets
import socket
import time
from collections.abc import Callable

PROTOCOL_VERSION = 1
_DEFAULT_DIAL_TIMEOUT_S = 2.0


class DaemonUnavailable(Exception):
    """No daemon answered within the caller's budget -- take the fallback path."""


def new_client_id() -> str:
    """A fresh opaque subscriber identity for one caller's lifetime."""
    return secrets.token_hex(8)


def _send_recv(host: str, port: int, token: str, message: dict, *, timeout: float) -> dict:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            envelope = dict(message, version=PROTOCOL_VERSION, token=token)
            sock.sendall(json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as exc:
        raise DaemonUnavailable(str(exc)) from exc
    if not buf:
        raise DaemonUnavailable("empty response")
    try:
        response = json.loads(buf.decode("utf-8"))
    except ValueError as exc:
        raise DaemonUnavailable("malformed response") from exc
    if not isinstance(response, dict) or response.get("version") != PROTOCOL_VERSION:
        raise DaemonUnavailable("malformed response")
    return response


def subscribe(
    host: str, port: int, token: str, client_id: str, *, timeout: float = _DEFAULT_DIAL_TIMEOUT_S
) -> None:
    resp = _send_recv(
        host, port, token, {"action": "subscribe", "client_id": client_id}, timeout=timeout
    )
    if not resp.get("ok"):
        raise DaemonUnavailable("subscribe rejected")


def release(
    host: str, port: int, token: str, client_id: str, *, timeout: float = _DEFAULT_DIAL_TIMEOUT_S
) -> None:
    """Best-effort: a failed release is backstopped by the server's own liveness reaper."""
    try:
        _send_recv(
            host, port, token, {"action": "release", "client_id": client_id}, timeout=timeout
        )
    except DaemonUnavailable:
        pass


def request(
    host: str,
    port: int,
    token: str,
    *,
    kind: str,
    key: str,
    payload: dict,
    request_deadline_s: float,
    client_id: str | None = None,
) -> dict:
    """Send one coalesced request. Raises ``DaemonUnavailable`` on any miss."""
    deadline = time.time() + request_deadline_s
    message: dict = {
        "action": "request",
        "kind": kind,
        "key": key,
        "payload": payload,
        "deadline": deadline,
    }
    if client_id:
        message["client_id"] = client_id
    resp = _send_recv(host, port, token, message, timeout=request_deadline_s + 1.0)
    if resp.get("fallback"):
        raise DaemonUnavailable("daemon reported fallback (deadline exceeded server-side)")
    result = resp.get("result")
    if not isinstance(result, dict):
        raise DaemonUnavailable("malformed response")
    return result


def call_with_fallback(
    *,
    dial: Callable[[], tuple[str, int, str] | None],
    boot: Callable[[], None] | None,
    boot_wait_s: float,
    kind: str,
    key: str,
    payload: dict,
    request_deadline_s: float,
    fallback: Callable[[], dict],
    client_id: str | None = None,
    poll_interval_s: float = 0.1,
) -> dict:
    """The full client sequence -- never raises past this call.

    1. ``dial()`` resolves rendezvous; ``None`` means no live daemon is
       currently discoverable.
    2. On a miss, invoke ``boot`` once (best-effort) and re-poll ``dial`` up
       to ``boot_wait_s`` -- the boot-wait budget.
    3. Send one coalesced request within ``request_deadline_s``.
    4. Any failure at any phase (no daemon, boot-wait timeout, request
       timeout/fallback/error) falls through to ``fallback()`` -- the
       always-optional, always-correct inline path.
    """
    started = time.time()
    endpoint = dial()
    if endpoint is None and boot is not None:
        boot()
        while endpoint is None and time.time() - started < boot_wait_s:
            time.sleep(poll_interval_s)
            endpoint = dial()
    if endpoint is None:
        return fallback()

    host, port, token = endpoint
    try:
        return request(
            host,
            port,
            token,
            kind=kind,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except DaemonUnavailable:
        return fallback()
