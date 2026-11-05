"""Content-free tool-start occurrences in one retained native EventLog range."""

from __future__ import annotations

from dataclasses import dataclass

MAX_EXACT_INTEGER = (1 << 53) - 1


@dataclass(frozen=True)
class ToolStartStatistics:
    """An owner projection, never unique calls, concurrency or lifetime totals."""

    start_occurrences: int | None = None
    observed_records: int | None = None
    floor: int | None = None
    head: int | None = None
    contiguous: bool | None = None
    unavailable_reason: str | None = None


@dataclass
class ToolStartCounter:
    """Fixed-size state updated only at the native record's admission seam."""

    start_occurrences: int = 0
    observed_records: int = 0
    floor: int = 0
    head: int = 0
    contiguous: bool = True

    def observe(self, event_id: int, kind: str) -> None:
        """Include one strictly increasing native record without its content."""
        if (
            type(event_id) is not int
            or not self.head < event_id <= MAX_EXACT_INTEGER
            or type(kind) is not str
            or not kind
            or self.observed_records >= MAX_EXACT_INTEGER
            or self.start_occurrences >= MAX_EXACT_INTEGER
        ):
            raise ValueError("tool observation range is not exactly representable")
        if self.observed_records == 0:
            self.floor = event_id
        elif event_id != self.head + 1:
            self.contiguous = False
        self.head = event_id
        self.observed_records += 1
        self.start_occurrences += kind == "tool_call_start"

    def snapshot(
        self, *, continuity: str | None, floor: int, head: int, records: int,
    ) -> ToolStartStatistics:
        """Qualify the counter against the same locked native source cut."""
        if not continuity or not records:
            return ToolStartStatistics(unavailable_reason="empty-or-unqualified-source")
        if (self.floor, self.head, self.observed_records) != (floor, head, records):
            return ToolStartStatistics(unavailable_reason="counter-source-mismatch")
        return ToolStartStatistics(
            self.start_occurrences, self.observed_records,
            self.floor, self.head, self.contiguous,
        )
