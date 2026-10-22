"""Optional PR-watch composition contract; only nonsecret identities cross owners."""

from __future__ import annotations

import math
import re

PROTOCOL = "delegated_run_waiters/v1"
NOTIFICATIONS = "acknowledged_notifications/v1"
CALLBACK_COMMAND = "_delegated-waiter-callback"
TRANSITIONS = frozenset({
    "merged", "closed", "review_changed", "mergeable_changed", "checks_changed",
})
DEFAULT_TIMEOUT = 259200.0
MAX_TIMEOUT = 2147483.0
RECOVERY_GRACE = 120.0


def positive_timeout(value: object) -> float:
    if (
        type(value) not in (int, float) or not 0 < value <= MAX_TIMEOUT
        or not math.isfinite(value)
    ):
        raise ValueError("PR-watch timeout must be finite, positive and at most 2147483 seconds")
    return float(value)


def validate_delegation(value: object, *, armed: bool = False) -> dict:
    fields = {"coordinator_id", "preparation_id", "subscriber_id", "repo", "number",
              "timeout", "until", "shared"}
    if isinstance(value, dict) and "installation" in value:
        from .delegated_waiter_context import validate_installation
        validate_installation(value["installation"])
        fields.add("installation")
    if not isinstance(value, dict) or set(value) != fields | ({"registration_id"} if armed else set()):
        raise ValueError("invalid delegated waiter identity")
    for field in ("coordinator_id", "preparation_id", "subscriber_id"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,200}", value[field]):
            raise ValueError("invalid delegated waiter identity")
    if not isinstance(value["repo"], str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", value["repo"]):
        raise ValueError("invalid PR repository")
    if type(value["number"]) is not int or not 0 < value["number"] < 2**31:
        raise ValueError("invalid PR number")
    positive_timeout(value["timeout"])
    if (
        not isinstance(value["until"], list) or not value["until"]
        or any(not isinstance(t, str) or t not in TRANSITIONS for t in value["until"])
        or type(value["shared"]) is not bool
    ):
        raise ValueError("invalid delegated waiter options")
    if armed and (
        not isinstance(value["registration_id"], str)
        or not value["registration_id"] or len(value["registration_id"]) > 200
    ):
        raise ValueError("invalid watch registration")
    return dict(value)


def validate_event(value: object, delegation: dict) -> dict:
    required = {"event_id", "registration_id", "notification_protocol", "repo",
                "number", "subscriber_id", "transitions", "timed_out"}
    snapshot = {"pr_state", "merged", "review_decision", "mergeable", "checks_state"}
    if not isinstance(value, dict) or set(value) not in (required, required | snapshot):
        raise ValueError("invalid watch callback")
    if (
        value["notification_protocol"] != NOTIFICATIONS
        or any(value[f] != delegation[f] for f in ("repo", "number", "subscriber_id"))
        or type(value["number"]) is not int
        or any(not isinstance(value[f], str) or not value[f] or len(value[f]) > 200
               for f in ("event_id", "registration_id"))
        or type(value["timed_out"]) is not bool
        or not isinstance(value["transitions"], list)
        or any(not isinstance(t, str) or t not in TRANSITIONS for t in value["transitions"])
        or any(t not in delegation["until"] for t in value["transitions"])
        or (value["timed_out"] and value["transitions"])
        or (not value["timed_out"] and not value["transitions"])
        or (value["transitions"] and not snapshot.issubset(value))
        or any(f in value and not isinstance(value[f], str)
               for f in snapshot - {"merged"})
        or ("merged" in value and type(value["merged"]) is not bool)
    ):
        raise ValueError("invalid watch callback")
    return dict(value)
