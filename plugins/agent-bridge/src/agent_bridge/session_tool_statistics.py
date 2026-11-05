"""Optional bounded wire projection of one atomic EventLog statistics cut."""

from __future__ import annotations

from typing import Any

from .event_tool_statistics import MAX_EXACT_INTEGER, ToolStartStatistics
from .session_observation_contract import ObservationError

_FIELDS = {
    "version", "availability", "reason", "basis", "coverage", "durability",
    "start_occurrences", "observed_records", "floor", "head", "contiguous",
}
_COUNTERS = ("start_occurrences", "observed_records", "floor", "head", "contiguous")
_UNKNOWN = {
    "empty-or-unqualified-source", "counter-source-mismatch", "counter-unavailable",
}
_BASIS = "native-tool-start-record-occurrences"


def project(
    statistics: ToolStartStatistics, source: dict[str, Any], retained: dict[str, Any],
) -> dict[str, Any]:
    """Keep ancillary malformed/unavailable statistics separate from source delivery."""
    reason = statistics.unavailable_reason
    body = {
        "version": 1,
        "availability": "unknown" if reason else "observed",
        "reason": reason if reason in _UNKNOWN else "native-event-range",
        "basis": _BASIS,
        "coverage": "partial",
        "durability": "unknown",
        **{key: getattr(statistics, key) for key in _COUNTERS},
    }
    try:
        validate(body, source, retained)
    except ObservationError:
        body.update(
            availability="unknown", reason="counter-unavailable",
            **dict.fromkeys(_COUNTERS),
        )
    return body


def validate(body: Any, source: dict[str, Any], retained: Any) -> None:
    """Reject imprecise counters, false completeness and cross-cut range claims."""
    if not isinstance(body, dict) or set(body) != _FIELDS:
        raise ObservationError("invalid-frame", "invalid tool statistics fields")
    if (
        type(body["version"]) is not int or body["version"] != 1
        or body["basis"] != _BASIS or body["coverage"] != "partial"
        or body["durability"] != "unknown"
    ):
        raise ObservationError("invalid-frame", "invalid tool statistics qualification")
    if body["availability"] == "unknown":
        if (
            not isinstance(body["reason"], str) or body["reason"] not in _UNKNOWN
            or any(body[key] is not None for key in _COUNTERS)
        ):
            raise ObservationError("invalid-frame", "invalid unknown tool statistics")
        return
    if (
        body["availability"] != "observed" or body["reason"] != "native-event-range"
        or not source.get("journal_incarnation")
        or not isinstance(retained, dict)
        or any(
            type(body[key]) is not int or not 0 <= body[key] <= MAX_EXACT_INTEGER
            for key in _COUNTERS[:-1]
        )
        or type(body["contiguous"]) is not bool
    ):
        raise ObservationError("invalid-frame", "invalid observed tool statistics")
    starts, records, floor, head = (body[key] for key in _COUNTERS[:-1])
    if (
        not 0 <= starts <= records or not 1 <= floor <= head
        or not 1 <= records <= head - floor + 1
        or body["contiguous"] != (records == head - floor + 1)
        or type(retained.get("floor")) is not int or retained["floor"] != floor
        or type(retained.get("head")) is not int or retained["head"] != head
    ):
        raise ObservationError("invalid-frame", "tool statistics range does not match source cut")
