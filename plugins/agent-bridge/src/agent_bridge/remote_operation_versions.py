"""Select the minimum bridge-owned carrier version for each request."""
from __future__ import annotations

from typing import Any

from .live_peer_address import peer_reply_requested

REMOTE_OPERATION_VERSION = 5
REMOTE_COPILOT_ARGS_MIN_VERSION = 3
REMOTE_PEER_REPLY_MIN_VERSION = 4
REMOTE_RECEIPT_REPLY_MIN_VERSION = 5


def request_operation_version(payload: dict[str, Any]) -> int:
    operation = payload.get("operation")
    if operation == "live_session.send" and payload.get("wait") is True:
        return REMOTE_RECEIPT_REPLY_MIN_VERSION
    if peer_reply_requested(payload):
        return REMOTE_PEER_REPLY_MIN_VERSION
    if payload.get("copilot_args") is not None:
        return REMOTE_COPILOT_ARGS_MIN_VERSION
    if operation in {"live_session.send", "session.create", "session.end", "session.stop"}:
        return 2
    return 1
