"""Qualified addresses for represented peers, independent of conversation ids."""

from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from argparse import Namespace
    from .client import BridgeClient
    from .db import Database

_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*")


@dataclass(frozen=True)
class LivePeerAddress:
    machine: str
    project: str
    worktree: str

    def __str__(self) -> str:
        return f"live:{self.machine}:{self.project}:{self.worktree}"

    @classmethod
    def parse(cls, handle: str) -> LivePeerAddress | None:
        if not handle.startswith("live:"):
            return None
        parts = handle.split(":")
        if (len(parts) != 4 or len(handle) > 256
                or _PART.fullmatch(parts[1]) is None
                or any(_PART.fullmatch(segment) is None for segment in parts[2].split("/"))
                or _PART.fullmatch(parts[3]) is None):
            raise ValueError("peer address must be live:<machine>:<project>:<worktree>")
        return cls(*parts[1:])

    @classmethod
    def from_registration(cls, row: Mapping[str, object]) -> LivePeerAddress | None:
        parts = [row.get(key) for key in ("machine", "repo", "worktree_id")]
        if any(not isinstance(part, str) or not part for part in parts):
            return None
        try:
            return cls.parse("live:" + ":".join(str(part) for part in parts))
        except ValueError:
            return None


def validate_live_handle(value: Any, *, field: str, qualified_peer: bool = False) -> str:
    from .remote_errors import RemoteBridgeError
    from .remote_operations import validate_session_id

    if not qualified_peer:
        try:
            return validate_session_id(value, field=field)
        except RemoteBridgeError:
            if not isinstance(value, str) or not value.startswith("live:"):
                raise
    if isinstance(value, str) and value.startswith("live:"):
        try:
            LivePeerAddress.parse(value)
        except ValueError as exc:
            raise RemoteBridgeError(400, "invalid_request", f"{field}: {exc}") from exc
        return value
    raise RemoteBridgeError(400, "invalid_request", f"{field}: expected a live peer address")


def peer_operation_timeout(timeout: float, *, wait: bool, wait_timeout: float) -> float:
    return max(timeout, wait_timeout + 15.0) if wait else timeout


def peer_target_after_lookup(target: str, prompt: str, *, live: bool) -> LivePeerAddress | None:
    from .send_outcome import SendRefused

    try:
        address = None if live else LivePeerAddress.parse(target)
    except ValueError as exc:
        raise SendRefused(
            "refused_unavailable", reason="invalid_address", retryable=False,
            target=target, error=str(exc),
        ) from exc
    if address is not None and not prompt.strip():
        raise SendRefused(
            "refused_unavailable", reason="empty_message", retryable=False,
            target=target, error="Refusing to deliver an empty peer message.",
        )
    return address


def caller_reply_address(args: Namespace, client_factory: Callable[[], BridgeClient],
                         worktree_handle: Callable[[], str | None]) -> str | None:
    import os
    import sys
    from .protocol import QUALIFIED_LIVE_PEER_PROTOCOL_VERSION

    explicit = getattr(args, "reply_to", None)
    if explicit:
        return explicit
    handle = worktree_handle()
    session = (os.environ.get("AGENT_BRIDGE_SESSION_ID") or os.environ.get("SESSION_ID")
               or os.environ.get("COPILOT_AGENT_SESSION_ID"))
    candidate = os.environ.get("COPILOT_AGENT_SESSION_ID") or session or handle
    if candidate:
        client = client_factory()
        if client.daemon_supports(QUALIFIED_LIVE_PEER_PROTOCOL_VERSION):
            address = LivePeerAddress.from_registration(client.resolve_live_session(candidate))
            if address is not None:
                return str(address)
    if candidate or handle:
        print("[!] Caller daemon or registration lacks qualified reply support; reply-to is local-only.",
              file=sys.stderr)
    return handle or session


def send_remote_peer(client: BridgeClient, args: Namespace, address: LivePeerAddress, *, sender: str,
                     reply_to: str | None, kind: str, delivery: str, prompt: str) -> dict[str, Any]:
    """Explicit peer targets never fall through to agent creation."""
    import sys

    from .send_outcome import SendRefused

    if reply_to and not getattr(args, "reply_to", None):
        try:
            qualified = LivePeerAddress.parse(reply_to)
        except ValueError:
            qualified = None
        if qualified is None:
            print("[!] Omitting a local-only automatic reply address from the remote message.",
                  file=sys.stderr)
            reply_to = None
    wait = getattr(args, "wait_reply", False) and not getattr(args, "no_wait", False)
    wait_timeout = getattr(args, "reply_timeout", 120.0) if wait else 120.0
    result = client.send_remote_live_message(
        address.machine, str(address), sender=sender, message=prompt,
        reply_to=reply_to, kind=kind, delivery=delivery, wait=wait,
        wait_timeout=wait_timeout,
        expected_session_id=getattr(args, "expected_session_id", None),
        idempotency_key=getattr(args, "idempotency_key", None),
        timeout=peer_operation_timeout(20.0, wait=wait, wait_timeout=wait_timeout),
        qualified_peer=True,
    )
    if not result.get("message_id"):
        raise SendRefused(
            "refused_unavailable", reason="invalid_receipt", retryable=False,
            target=str(address), error="Remote peer delivery returned no message receipt.",
        )
    return result


def resolve_qualified_peer(db: Database, address: LivePeerAddress, *, now: float,
                           stale_seconds: float) -> dict[str, Any] | None:
    rows = db.execute_read(
        "SELECT * FROM live_sessions WHERE machine=? COLLATE NOCASE "
        "AND repo=? COLLATE NOCASE AND worktree_id=? AND status='live' "
        "AND updated_at>=? ORDER BY registered_at DESC, updated_at DESC LIMIT 1",
        (address.machine, address.project, address.worktree, now - stale_seconds),
    )
    return dict(rows[0]) if rows else None


def current_worktree_session(db: Database, worktree: str, *, now: float, stale_seconds: float,
                             machine: str | None = None, project: str | None = None,
                             include_wedged: bool = False) -> str | None:
    """Order incarnations by registration, never by a later old-head heartbeat.

    Fresh live rows serve messaging; wedged rows are readable only when explicitly
    requested for result inspection. Expired takeover rows never regain current.
    Missing identity scope retains the legacy unqualified worktree lookup.
    """
    status = "(status='live' AND updated_at>=?)"
    if include_wedged:
        status = f"(status='wedged' OR {status})"
    rows = db.execute_read(
        f"SELECT session_id FROM live_sessions WHERE worktree_id=? AND {status} "
        "AND (? IS NULL OR machine=? COLLATE NOCASE) "
        "AND (? IS NULL OR repo=? COLLATE NOCASE) "
        "ORDER BY registered_at DESC, updated_at DESC LIMIT 1",
        (worktree, now - stale_seconds, machine, machine, project, project),
    )
    return rows[0]["session_id"] if rows else None


def peer_reply_requested(payload: dict[str, Any]) -> bool:
    return (
        payload.get("qualified_peer") is True
        or payload.get("reply_to") is not None or payload.get("wait") is True
    )


def peer_wait_timeout(value: Any) -> float:
    from .live_message_models import validate_live_reply_timeout
    from .remote_errors import RemoteBridgeError
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RemoteBridgeError(400, "invalid_request", "wait_timeout must be a finite number between 0 and 600 seconds")
    try:
        return validate_live_reply_timeout(value)
    except ValueError as exc:
        raise RemoteBridgeError(400, "invalid_request", f"wait_timeout {exc}") from exc


async def require_peer_reply_protocol(client: BridgeClient) -> None:
    from .protocol import QUALIFIED_LIVE_PEER_PROTOCOL_VERSION
    from .remote_errors import RemoteBridgeError
    if not await asyncio.to_thread(client.daemon_supports, QUALIFIED_LIVE_PEER_PROTOCOL_VERSION):
        raise RemoteBridgeError(
            426, "unsupported_version", f"the hosting Bridge requires HTTP protocol {QUALIFIED_LIVE_PEER_PROTOCOL_VERSION} for peer replies",
        )


async def deliver_remote_peer(client: BridgeClient, payload: dict[str, Any]) -> dict[str, Any]:
    from .remote_operations import (
        MAX_IDEMPOTENCY_KEY_LENGTH, MAX_SESSION_ID_LENGTH, LIVE_MESSAGE_DELIVERIES,
        validate_caller_id, validate_message, validate_optional_bool,
        validate_optional_id, validate_session_id,
    )
    from .remote_errors import RemoteBridgeError

    if peer_reply_requested(payload):
        await require_peer_reply_protocol(client)
    qualified_peer = validate_optional_bool(payload, "qualified_peer")
    target = validate_live_handle(payload.get("target"), field="target", qualified_peer=qualified_peer)
    sender = validate_caller_id(payload.get("sender"), field="sender")
    message = validate_message(payload.get("message"), field="message")
    idempotency_key = validate_optional_id(
        payload.get("idempotency_key"), field="idempotency_key", maximum=MAX_IDEMPOTENCY_KEY_LENGTH,
    )
    expected_session_id = validate_optional_id(
        payload.get("expected_session_id"), field="expected_session_id", maximum=MAX_SESSION_ID_LENGTH,
    )
    reply_to = payload.get("reply_to")
    if reply_to is not None:
        reply_to = validate_live_handle(reply_to, field="reply_to")
    wait = validate_optional_bool(payload, "wait")
    reply_options: dict[str, Any] = {}
    if reply_to is not None:
        reply_options["reply_to"] = reply_to
    if wait:
        reply_options["wait_timeout"] = peer_wait_timeout(payload.get("wait_timeout", 120.0))
    live = await asyncio.to_thread(
        client.resolve_live_session, target, **({"qualified_peer": True} if qualified_peer else {}),
    )
    if not live:
        raise RemoteBridgeError(404, "session_not_found", f"live session target {target} was not found")
    session_id = validate_session_id(live.get("session_id"))
    kind = payload.get("kind") or "prompt"
    if kind not in {"notify", "prompt", "status-check"}:
        raise RemoteBridgeError(400, "invalid_request", "kind is not supported")
    delivery = payload.get("delivery") or "queue"
    if delivery not in LIVE_MESSAGE_DELIVERIES:
        raise RemoteBridgeError(400, "invalid_request", "delivery is not supported")
    return await asyncio.to_thread(
        client.send_live_message, session_id, sender=sender, body=message, kind=kind,
        delivery=delivery, wait=wait, idempotency_key=idempotency_key,
        expected_session_id=expected_session_id, **reply_options,
    )
