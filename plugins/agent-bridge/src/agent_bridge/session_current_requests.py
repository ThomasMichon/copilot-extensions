"""Optional live-waiter wire projection over an exact already-owned session."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from .acp_client import AcpClient
from .session_observation_contract import ObservationError, encode
from .session_status import SessionStatus

MAX_BYTES = 8 * 1024
_FIELDS = {
    "version", "client_incarnation", "revision", "availability", "completeness",
    "reason", "observed_at", "requests",
}
_UNKNOWN = {
    "client-disconnected", "unsupported-transport", "producer-replaced",
    "unsupported-source", "no-current-client", "session-unavailable", "session-mismatch",
}


def current_client(known: Any) -> AcpClient | None:
    """Return no actor/proxy lookup fallback for represented or missing clients."""
    client = getattr(known.session, "client", None) if known.registry == "owned" else None
    return client if isinstance(client, AcpClient) else None


def snapshot(known: Any) -> dict[str, Any]:
    """Qualify the current owner/transport/session before asserting any waiters."""
    client = current_client(known)
    if client is None:
        body = {
            "version": 1, "client_incarnation": None, "revision": None,
            "availability": "unknown", "completeness": "unknown",
            "reason": "unsupported-source" if known.registry != "owned" else "no-current-client",
            "observed_at": datetime.now(timezone.utc).isoformat(), "requests": [],
        }
    else:
        reason = None
        if known.session.target.type != "local":
            reason = "unsupported-transport"
        elif (
            getattr(known.session, "status", None) not in {SessionStatus.RUNNING, SessionStatus.IDLE}
            or getattr(known.session, "_stop_requested", False)
        ):
            reason = "session-unavailable"
        elif known.source["durable_session_id"] != client.acp_session_id:
            reason = "session-mismatch"
        body = client.current_requests.snapshot(client, unavailable_reason=reason).as_dict()
    validate(body)
    return body


def _identity(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def validate(body: Any) -> None:
    """Accept only the bounded optional projection, never extra answering data."""
    if not isinstance(body, dict) or set(body) != _FIELDS or len(encode(body)) > MAX_BYTES:
        raise ObservationError("invalid-frame", "invalid current request state fields or size")
    availability, completeness, reason = (
        body["availability"], body["completeness"], body["reason"]
    )
    if (
        type(body["version"]) is not int or body["version"] != 1
        or not isinstance(reason, str)
        or not (
            (availability == "unknown" and completeness == "unknown" and reason in _UNKNOWN)
            or (availability == "observed" and completeness == "complete"
                and reason == "local-acp-waiters")
            or (availability == "observed" and completeness == "partial"
                and reason in {"waiter-budget", "waiter-coverage"})
        )
    ):
        raise ObservationError("invalid-frame", "invalid current request qualification")
    incarnation, revision = body["client_incarnation"], body["revision"]
    if not (
        (_identity(incarnation) and type(revision) is int and revision > 0)
        or (availability == "unknown" and incarnation is None and revision is None)
    ):
        raise ObservationError("invalid-frame", "invalid current request revision identity")
    observed_at = body["observed_at"]
    try:
        if not isinstance(observed_at, str) or len(observed_at) > 64:
            raise ValueError
        if datetime.fromisoformat(observed_at.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError
    except ValueError as exc:
        raise ObservationError("invalid-frame", "invalid current request observation time") from exc
    requests = body["requests"]
    if (
        not isinstance(requests, list) or len(requests) > 32
        or (availability == "unknown" and requests)
    ):
        raise ObservationError("invalid-frame", "invalid current request inventory")
    seen = set()
    for item in requests:
        if (
            not isinstance(item, dict) or set(item) != {"occurrence_id", "kind"}
            or not _identity(item["occurrence_id"])
            or not isinstance(item["kind"], str) or item["kind"] not in {"input", "permission"}
            or item["occurrence_id"] in seen
        ):
            raise ObservationError("invalid-frame", "invalid current request occurrence")
        seen.add(item["occurrence_id"])


def content_key(body: dict[str, Any] | None) -> bytes | None:
    """Observation-clock-only changes do not create state updates."""
    return encode({key: value for key, value in body.items() if key != "observed_at"}) if body else None
