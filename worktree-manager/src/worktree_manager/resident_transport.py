"""Bounded, cancellation-owned transport for the resident's existing feed."""

from __future__ import annotations

import json
import select
import socket
import time
from pathlib import Path
from machine_transport import detect_platform

from .worktree_feed_contract import FeedUnavailable

_MAX_FRAME_BYTES = 80 * 1024


def _request(token: str, action: str, payload: dict) -> bytes:
    return json.dumps({
        "version": 1, "token": token, "action": action, "payload": payload,
    }, separators=(",", ":")).encode("utf-8") + b"\n"


def _health_line(sock, *, alive, deadline: float) -> bytes | None:
    pending = bytearray()
    while alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("resident health response timed out")
        readable, _, _ = select.select([sock], [], [], min(remaining, 0.1))
        if not readable:
            continue
        chunk = sock.recv(8192)
        if not chunk:
            raise FeedUnavailable("resident authentication/health response is missing", retry=False)
        pending.extend(chunk)
        if len(pending) > _MAX_FRAME_BYTES:
            raise FeedUnavailable("resident health response exceeds its bound", retry=False)
        if b"\n" in pending:
            return bytes(pending)
    return None


def frames(root: Path, project: str, *, alive, startup_timeout: float):
    from zdd import routing

    startup_deadline = time.monotonic() + startup_timeout
    endpoint = routing.read_active_endpoint(root / "status-monitor-routing")
    if endpoint is None:
        raise FeedUnavailable("resident current-roster endpoint is unavailable")
    if endpoint.client_host not in {"127.0.0.1", "::1"}:
        raise FeedUnavailable("local resident route is not loopback", retry=False)
    token = (root / "status-monitor-control.token").read_text(encoding="utf-8").strip()
    if not token:
        raise FeedUnavailable("resident owner token is unavailable", retry=False)
    try:
        with socket.create_connection(
            (endpoint.client_host, endpoint.port), timeout=min(startup_timeout, 0.25),
        ) as health:
            health.sendall(_request(token, "health", {}))
            pending = _health_line(health, alive=alive, deadline=startup_deadline)
        if pending is None:
            return
        response = json.loads(pending)
        state = response.get("result") if isinstance(response, dict) else None
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("capabilities"), list)
            or "worktrees-feed-v1" not in state["capabilities"]
        ):
            raise FeedUnavailable("resident lacks worktrees-feed-v1; update the engine", retry=False)
        if state.get("status") == "draining":
            raise FeedUnavailable("resident is draining")
        payload = {
            "contract_version": 1, "project": project, "worktree_id": None,
            "codename": None, "status_filter": None,
            "platform_filter": detect_platform(), "include_missing": False,
        }
        with socket.create_connection(
            (endpoint.client_host, endpoint.port), timeout=min(startup_timeout, 0.25),
        ) as sock:
            sock.sendall(_request(token, "worktrees-feed", payload))
            pending = bytearray()
            deadline = startup_deadline
            in_transaction = False
            while alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FeedUnavailable("resident feed missed its startup/publication/heartbeat deadline")
                readable, _, _ = select.select([sock], [], [], min(remaining, 0.1))
                if not readable:
                    continue
                chunk = sock.recv(65536)
                if not chunk:
                    raise FeedUnavailable("resident feed disconnected without a going-away notice")
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if len(line) > _MAX_FRAME_BYTES:
                        raise ValueError("resident feed exceeded its frame bound")
                    if time.monotonic() >= deadline:
                        raise FeedUnavailable("resident feed exceeded its publication deadline")
                    frame = json.loads(line)
                    if not isinstance(frame, dict):
                        raise ValueError("resident feed emitted a non-object frame")
                    kind = frame.get("type")
                    if kind in {"begin", "update"}:
                        if not in_transaction:
                            deadline = time.monotonic() + 2.0
                        in_transaction = True
                    elif kind in {"done", "commit"}:
                        in_transaction = False
                        deadline = time.monotonic() + 5.0
                    elif not in_transaction and kind in {"heartbeat", "availability"}:
                        deadline = time.monotonic() + 5.0
                    yield frame
                    if not alive() or kind in {"going-away", "error"}:
                        return
                if len(pending) > _MAX_FRAME_BYTES:
                    raise ValueError("resident feed exceeded its frame bound")
    except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError) as error:
        raise FeedUnavailable(f"resident feed disconnected: {error}") from error
    except TimeoutError as error:
        raise FeedUnavailable("resident live-peer response timed out", retry=False) from error
