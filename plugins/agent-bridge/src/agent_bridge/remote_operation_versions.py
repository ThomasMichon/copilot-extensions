"""Select the minimum bridge-owned carrier version for each request."""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from .live_peer_address import peer_reply_requested
from .remote_errors import RemoteBridgeError

REMOTE_OPERATION_VERSION = 5
REMOTE_COPILOT_ARGS_MIN_VERSION = 3
REMOTE_PEER_REPLY_MIN_VERSION = 4
REMOTE_RECEIPT_REPLY_MIN_VERSION = 5
_REMOTE_OPERATION_VERSIONS = {1, 2, 3, 4, REMOTE_OPERATION_VERSION}


def validate_carrier_request(
    payload: dict[str, Any], *, operations: Collection[str],
) -> tuple[str, dict[str, Any]]:
    """Keep receiving version checks with the existing operation-version owner."""
    operation = payload.get("operation")
    if not isinstance(operation, str) or operation not in operations:
        raise RemoteBridgeError(501, "unsupported_operation", "carrier operation is not available")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in _REMOTE_OPERATION_VERSIONS:
        raise RemoteBridgeError(
            426, "unsupported_version", "remote operation version is not supported",
            details={"supported_version": REMOTE_OPERATION_VERSION, "requested_version": version},
        )
    if version == 1 and operation in {
        "live_session.send", "session.native_prompt", "live_session.exact_prompt",
        "session.create", "session.end", "session.stop",
    }:
        raise RemoteBridgeError(
            426, "unsupported_version", "remote operation requires protocol version 2",
            details={"supported_version": REMOTE_OPERATION_VERSION, "requested_version": version},
        )
    if (
        version < REMOTE_COPILOT_ARGS_MIN_VERSION
        and operation == "session.create"
        and payload.get("copilot_args") is not None
    ):
        raise RemoteBridgeError(
            426, "unsupported_version",
            "remote operation requires protocol version "
            f"{REMOTE_COPILOT_ARGS_MIN_VERSION} for copilot_args",
            details={"supported_version": REMOTE_OPERATION_VERSION, "requested_version": version},
        )
    if peer_reply_requested(payload) and version < REMOTE_PEER_REPLY_MIN_VERSION:
        raise RemoteBridgeError(426, "unsupported_version", "peer replies require carrier operation version 4")
    return operation, payload


def request_operation_version(payload: dict[str, Any]) -> int:
    operation = payload.get("operation")
    if operation == "live_session.send" and payload.get("wait") is True:
        return REMOTE_RECEIPT_REPLY_MIN_VERSION
    if peer_reply_requested(payload):
        return REMOTE_PEER_REPLY_MIN_VERSION
    if payload.get("copilot_args") is not None:
        return REMOTE_COPILOT_ARGS_MIN_VERSION
    if operation in {
        "live_session.send", "session.native_prompt", "live_session.exact_prompt",
        "session.create", "session.end", "session.stop",
    }:
        return 2
    return 1
