"""Strict optional control actions; no discovery, startup or caller-side fallback."""

from __future__ import annotations

from . import owner_observation_contract as contract

READ_ACTION = "worktree-observations-read"
HISTORY_ACTION = "worktree-observations-history"
FEED_ACTION = "worktree-observations-feed"
POINT_ACTIONS = frozenset({READ_ACTION, HISTORY_ACTION})
ACTIONS = POINT_ACTIONS | {FEED_ACTION}
CAPABILITIES = (contract.FEED_CAPABILITY, contract.HISTORY_CAPABILITY)
POINT_BYTES = contract.FRAME_BYTES + 64
HEARTBEAT_SECONDS = 0.5


def decode_request(raw: bytes, action: str) -> dict:
    value = contract.decode(raw)
    contract.exact(value, {"version", "token", "action", "payload"})
    if not raw.endswith(b"\n") or type(value["version"]) is not int or value["version"] != 1:
        raise contract.ObservationError("invalid observation control envelope")
    if value["action"] != action or action not in ACTIONS:
        raise contract.ObservationError("invalid observation control action")
    if not isinstance(value["token"], str) or not value["token"]:
        raise contract.ObservationError("invalid observation control token")
    contract.request(value["payload"], history=action == HISTORY_ACTION)
    return value


def encode_frame(value: dict) -> bytes:
    contract.validate_frame(value)
    return contract.utf8(contract.encode(value)) + b"\n"


def encode_result(value: dict) -> bytes:
    contract.validate_frame(value)
    return contract.utf8(contract.encode({"version": 1, "result": value}, POINT_BYTES)) + b"\n"


def decode_result(raw: bytes) -> dict:
    value = contract.decode(raw, POINT_BYTES)
    if not raw.endswith(b"\n"):
        raise contract.ObservationError("incomplete observation result", "invalid-response")
    contract.exact(value, {"version", "result"})
    if type(value["version"]) is not int or value["version"] != 1:
        raise contract.ObservationError("invalid observation result envelope", "invalid-response")
    contract.validate_frame(value["result"])
    return value["result"]
