"""Versioned callback wire contract; no consumer-specific lifecycle knowledge."""

from __future__ import annotations

import json
import math
import subprocess
import copy

from agent_procutil import no_window_kwargs
from .watch_contract import ALL_TRANSITIONS

ACKNOWLEDGED_NOTIFICATIONS = "acknowledged_notifications/v1"
CALLBACK_TIMEOUT = 30.0


def positive_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number")
    return float(value)


def validate_notify(spec: object, acknowledged: bool) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("notify must be an object")
    if acknowledged and set(spec) - {"argv", "timeout"}:
        raise ValueError("unsupported acknowledged notify option")
    argv = spec.get("argv")
    if acknowledged or argv is not None:
        if (
            not isinstance(argv, list) or not argv
            or any(not isinstance(a, str) or not a or "\0" in a for a in argv)
        ):
            raise ValueError("notify argv must be a nonempty list of nonempty strings")
    if "timeout" in spec:
        if not acknowledged:
            raise ValueError("notify timeout requires acknowledged notifications")
        if positive_seconds(spec["timeout"]) > CALLBACK_TIMEOUT:
            raise ValueError("callback timeout must not exceed 30 seconds")
    return json.loads(json.dumps(spec, allow_nan=False))


def event_payload(event) -> dict:
    if event.subscriber.pending is not None:
        return copy.deepcopy(event.subscriber.pending["payload"])
    payload = {
        "repo": event.key.repo,
        "number": event.key.number,
        "subscriber_id": event.subscriber.subscriber_id,
        "transitions": list(event.transitions),
        "timed_out": event.timed_out,
    }
    if event.snapshot is not None:
        for name in (
            "pr_state", "merged", "review_decision", "mergeable", "checks_state",
        ):
            payload[name] = getattr(event.snapshot, name)
    return payload


def default_notify(event) -> int | None:
    """Exit zero acknowledges durable callbacks; legacy callbacks remain best-effort."""
    spec = event.subscriber.notify
    argv = spec.get("argv") if isinstance(spec, dict) else None
    if not argv or not isinstance(argv, list):
        return None
    result = subprocess.run(  # noqa: S603 -- explicit subscriber callback argv
        [str(a) for a in argv],
        input=json.dumps(event_payload(event)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=spec.get("timeout", CALLBACK_TIMEOUT),
        check=False,
        **no_window_kwargs(),
    )
    return result.returncode


def validate_pending(pending: object, key, sub) -> None:
    """Fail closed on corrupt opted-in state, without echoing persisted inputs."""
    if not isinstance(pending, dict):
        raise ValueError("invalid acknowledged delivery state")
    payload = pending.get("payload")
    if (
        not isinstance(payload, dict)
        or not isinstance(pending.get("event_id"), str) or not pending["event_id"]
        or payload.get("event_id") != pending["event_id"]
        or payload.get("registration_id") != sub.registration_id
        or payload.get("notification_protocol") != ACKNOWLEDGED_NOTIFICATIONS
        or payload.get("repo") != key.repo or payload.get("number") != key.number
        or payload.get("subscriber_id") != sub.subscriber_id
        or not isinstance(payload.get("transitions"), list)
        or any(t not in ALL_TRANSITIONS for t in payload["transitions"])
        or not isinstance(payload.get("timed_out"), bool)
        or (not payload["timed_out"] and not payload["transitions"])
        or any(
            field in payload and not isinstance(payload[field], str)
            for field in ("pr_state", "review_decision", "mergeable", "checks_state")
        )
        or ("merged" in payload and not isinstance(payload["merged"], bool))
        or type(pending.get("attempts")) is not int or pending["attempts"] < 0
        or type(pending.get("next_attempt")) not in (int, float)
        or not math.isfinite(pending["next_attempt"]) or pending["next_attempt"] < 0
        or pending.get("last_error") not in (
            None, "callback_nonzero", "callback_timeout", "callback_error",
        )
    ):
        raise ValueError("invalid acknowledged delivery state")
    json.dumps(pending, allow_nan=False)
