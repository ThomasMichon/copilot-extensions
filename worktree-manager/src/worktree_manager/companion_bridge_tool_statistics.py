"""Content-free owner statistics, never inferred from the loaded timeline."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .companion_timeline_contract import TimelineContractError
from .companion_worktree_codec import encoded, object_fields

CAPABILITY = "bridge-session-tool-statistics-v1"
MAX_EXACT_INTEGER = (1 << 53) - 1
FIELDS = frozenset(
    "version availability reason basis coverage durability start_occurrences "
    "observed_records floor head contiguous".split()
)
UNKNOWN_REASONS = frozenset({
    "empty-or-unqualified-source", "counter-source-mismatch", "counter-unavailable",
})


@dataclass(frozen=True, slots=True)
class ToolStatisticsState:
    source: str
    reason: str
    start_occurrences: int | None
    observed_records: int | None
    floor: int | None
    head: int | None
    contiguous: bool | None

    def compact(self) -> str:
        if self.start_occurrences is None:
            return "Tool starts unknown"
        return f"{self.start_occurrences} starts (observed range)"

    def summary(self) -> str:
        if self.start_occurrences is None:
            return f"Tool starts unknown ({self.reason}); source {self.source}."
        return (
            f"{self.start_occurrences} native tool-start record occurrences in "
            f"{self.observed_records} observed records; range {self.floor}..{self.head}; "
            f"contiguous: {self.contiguous}; partial coverage; durability unknown. "
            "Not unique calls, running work or lifetime totals. "
            f"Source {self.source}."
        )


def decode_tool_statistics(
    value: Any, source: dict[str, Any], retained: dict[str, Any],
) -> ToolStatisticsState:
    body = object_fields(value, FIELDS)
    if (
        type(body["version"]) is not int or body["version"] != 1
        or body["basis"] != "native-tool-start-record-occurrences"
        or body["coverage"] != "partial" or body["durability"] != "unknown"
    ):
        raise TimelineContractError("invalid tool statistics qualification")
    counters = ("start_occurrences", "observed_records", "floor", "head")
    if body["availability"] == "unknown":
        if (
            not isinstance(body["reason"], str) or body["reason"] not in UNKNOWN_REASONS
            or any(body[key] is not None for key in (*counters, "contiguous"))
        ):
            raise TimelineContractError("invalid unknown tool statistics")
    elif (
        body["availability"] != "observed" or body["reason"] != "native-event-range"
        or not source["journal_incarnation"]
        or any(type(body[key]) is not int or not 0 <= body[key] <= MAX_EXACT_INTEGER
               for key in counters)
        or type(body["contiguous"]) is not bool
    ):
        raise TimelineContractError("invalid observed tool statistics")
    else:
        starts, records, floor, head = (body[key] for key in counters)
        if (
            not 0 <= starts <= records or not 1 <= floor <= head
            or not 1 <= records <= head - floor + 1
            or body["contiguous"] != (records == head - floor + 1)
            or floor != retained["floor"] or head != retained["head"]
        ):
            raise TimelineContractError("tool statistics changed its atomic source range")
    return ToolStatisticsState(
        sha256(encoded(source).encode()).hexdigest(), body["reason"],
        *(body[key] for key in counters), body["contiguous"],
    )


class ToolStatisticsProjection:
    def __init__(self) -> None:
        self.current: ToolStatisticsState | None = None
        self._last: ToolStatisticsState | None = None

    def invalidate(self) -> None:
        self.current = None

    def accept(self, state: ToolStatisticsState) -> bool:
        prior = self._last
        if prior is not None and prior.source == state.source and state.head is not None:
            assert prior.head is not None and prior.floor is not None and state.floor is not None
            if (
                state.head < prior.head or state.floor < prior.floor
                or (state.head == prior.head and state.floor == prior.floor and state != prior)
                or (state.floor == prior.floor and (
                    state.start_occurrences < prior.start_occurrences
                    or state.observed_records < prior.observed_records
                    or state.start_occurrences - prior.start_occurrences
                    > state.observed_records - prior.observed_records
                    or state.observed_records - prior.observed_records > state.head - prior.head
                ))
            ):
                raise TimelineContractError("tool statistics regressed or changed its source cut")
        changed = self.current != state
        self.current = state
        if state.head is not None:
            self._last = state
        return changed
