"""Closed structural projection; legacy Bridge reducer records stay unchanged."""

import math
from typing import Any

_KINDS = {"state_transition", "event", "error"}
_NAMES = {"session", "session_health", "conversation", "tool_call", "event_log"}
_EVENTS = {
    "session_state_changed", "connect_failed", "error", "context_warning",
    "context_critical", "user_message", "agent_message", "agent_thought",
    "turn_complete", "tool_call", "tool_call_start", "tool_call_update",
    "tool_call_progress", "log_rebuilt",
}
_STATES = {
    "idle", "sending", "responding", "running", "end-turn", "completed",
    "cancelled", "error", "stopped", "ended", "failed", "starting",
    "connecting", "pending",
}
_ENUMS = {
    **{key: _STATES for key in ("from", "to", "session_state", "conversation_state")},
    "tool_status": {"completed", "cancelled", "error"},
    "trigger": {"daemon_restart", "handoff", "interrupt", "resume", "resync", "other"},
    "stop_reason": {
        "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled",
        "canceled", "interrupted", "other",
    },
}
_FIELDS = frozenset({
    "kind", "name", "event", "event_id", "event_count",
    "context_pct", "threshold", "retryable", *_ENUMS,
})


def offered_fields(event: dict[str, Any]) -> dict[str, Any]:
    """Bound key inspection and omit unknown values without traversing them."""
    if type(event) is not dict or len(event) > 64:
        raise ValueError("unbounded source")
    result: dict[str, Any] = {}
    for key, value in event.items():
        if type(key) is not str:
            raise ValueError("nonprimitive key")
        if key in _FIELDS and (value is not None or key in ("kind", "name", "event")):
            result[key] = value
    name = result.get("name")
    if type(name) is str and name == "event_log":
        result.pop("from", None)
        result.pop("to", None)
    return result


def project(event: dict[str, Any]) -> dict[str, Any]:
    """Omit identities/unknown fields; reject invalid admitted primitive values."""
    result: dict[str, Any] = {}
    for key, allowed in (("kind", _KINDS), ("name", _NAMES), ("event", _EVENTS)):
        value = event.get(key)
        if type(value) is not str or value not in allowed:
            raise ValueError("unadmitted record")
        result[key] = value
    for key, allowed in _ENUMS.items():
        if result["name"] == "event_log" and key in ("from", "to"):
            continue
        value = event.get(key)
        if value is not None:
            if type(value) is not str or value not in allowed:
                raise ValueError("unadmitted enum")
            result[key] = value
    for key in ("event_id", "event_count"):
        value = event.get(key)
        if value is not None:
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                raise ValueError("unadmitted cursor")
            result[key] = value
    for key in ("context_pct", "threshold"):
        value = event.get(key)
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("unadmitted health")
            result[key] = value
    if event.get("retryable") is not None:
        if type(event["retryable"]) is not bool:
            raise ValueError("unadmitted retryable")
        result["retryable"] = event["retryable"]
    return result
