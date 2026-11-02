"""Height-indexed literal timeline rows; no widgets, strips or source I/O."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from rich.cells import cell_len

from .companion_timeline_contract import (
    EventRef, MAX_DETAIL_LINES, MAX_LANES, OVERSCAN_LINES, TimelineEvent, ViewportAnchor, literal_display,
)
from .companion_timeline_model import TimelineWindow


def display_text(text: str) -> str:
    return "".join(
        f"\\x{ord(character):02x}" if 0x80 <= ord(character) <= 0x9F else character
        for character in literal_display(text)
    )


def wrap_lines(text: str, width: int) -> Iterator[tuple[str, int]]:
    """Fold at terminal cell boundaries, retaining original logical line identity."""
    width = max(1, width)
    for logical, line in enumerate(display_text(text).expandtabs(4).split("\n")):
        part, cells = "", 0
        for character in line:
            size = cell_len(character)
            if cells + size > width and part:
                yield part, logical
                part, cells = "", 0
            # A two-cell glyph cannot fit a one-cell viewport.
            if size > width:
                character, size = "?", 1
            part += character
            cells += size
        yield part, logical


@dataclass(frozen=True, slots=True)
class TimelineRow:
    event: EventRef
    text: str
    logical_line: int
    lane: int
    heading: bool
    character_offset: int


@dataclass(frozen=True, slots=True)
class EventBlock:
    event: TimelineEvent
    start: int
    height: int
    heading_rows: int
    detail_rows: int
    heading_text: str
    detail_text: str
    heading_clipped: bool
    detail_clipped: bool


class TimelineLayout:
    def __init__(self) -> None:
        self.width = 1
        self._lane_count = 1
        self.prefix_width = 1
        self.text_width = 1
        self.height = 0
        self.blocks: tuple[EventBlock, ...] = ()
        self._starts: tuple[int, ...] = ()
        self._refs: dict[EventRef, EventBlock] = {}
        self.materialized_rows = 0

    @staticmethod
    def _time(event: TimelineEvent, now: datetime | None) -> str:
        stamp = event.occurred_at or event.observed_at
        qualifier = "" if event.occurred_at is not None else "observed "
        value = qualifier + stamp.isoformat(timespec="seconds")
        if now is not None:
            age = max(0, int((now - stamp).total_seconds()))
            value += f" ({age}s)"
        return value

    @staticmethod
    def _count(text: str, width: int, limit: int) -> tuple[int, bool]:
        count = 0
        for _line, _logical in wrap_lines(text, width):
            count += 1
            if count > limit:
                return limit, True
        return count, False

    @staticmethod
    def _details(event: TimelineEvent, window: TimelineWindow) -> str:
        lines = [
            f"Evidence: {event.evidence_kind.value}; durability: {event.durability.value}",
            f"Actor: {event.actor.kind.value}; qualification: {event.actor.qualification.value}",
            f"Position: {event.position.cursor}",
        ]
        if event.reported_phase is not None:
            lines.append(f"Reported phase: {event.reported_phase}")
        if event.details:
            lines.append(event.details)
        if event.data_json is not None:
            lines.append(f"Data: {event.data_json}")
        if event.payload_ref is not None:
            lines.append(f"Payload reference (not followed): {event.payload_ref}")
        for relation in event.relations:
            target = relation.target
            if target is not None:
                reference = f"{target.source_key}:{target.event_id}"
                reference += " [loaded]" if window.event(target) is not None else " [outside loaded window]"
            else:
                reference = relation.reference or "unknown reference"
            lines.append(f"{relation.kind.value} ({relation.qualification.value}): {reference}")
        children = window.children(event.ref)
        for child in children[:MAX_DETAIL_LINES]:
            lines.append(f"Linked child event (not allocation proof): {child.event_id} / {child.summary[:240]}")
        if len(children) > MAX_DETAIL_LINES:
            lines.append("Additional linked child events omitted from this bounded detail group.")
        return "\n".join(lines)

    def rebuild(self, window: TimelineWindow, width: int, *, now: datetime | None = None) -> None:
        self.width = max(1, width)
        self._lane_count = window.lane_count
        wide_prefix = self._lane_count * 2
        self.prefix_width = wide_prefix if self.width >= wide_prefix + 12 else min(6, self.width - 1)
        self.text_width = max(1, self.width - self.prefix_width)
        blocks = []
        start = 0
        for event in window.events:
            heading = (
                f"{self._time(event, now)} | {event.kind.value} | "
                f"{event.actor.label}: {event.summary}"
            )
            if window.lane(event) == MAX_LANES - 1:
                heading = "Other actors (overflow lane) | " + heading
            heading_rows, heading_clipped = self._count(heading, self.text_width, OVERSCAN_LINES)
            details = self._details(event, window) if event.ref in window.expanded else ""
            detail_rows, detail_clipped = self._count(details, self.text_width, MAX_DETAIL_LINES) if details else (0, False)
            height = heading_rows + detail_rows
            block = EventBlock(
                event, start, height, heading_rows, detail_rows, heading, details,
                heading_clipped, detail_clipped,
            )
            blocks.append(block)
            start += height
        self.blocks, self.height = tuple(blocks), start
        self._starts = tuple(block.start for block in blocks)
        self._refs = {block.event.ref: block for block in blocks}
        self.materialized_rows = 0

    def row(self, index: int, window: TimelineWindow) -> TimelineRow | None:
        if index < 0 or index >= self.height:
            return None
        block = self.blocks[bisect_right(self._starts, index) - 1]
        offset = index - block.start
        heading = offset < block.heading_rows
        text = block.heading_text if heading else block.detail_text
        wanted = offset if heading else offset - block.heading_rows
        character_offset, previous_logical = 0, -1
        rendered, logical = "", 0
        for number, (value, logical) in enumerate(wrap_lines(text, self.text_width)):
            if logical != previous_logical:
                character_offset = 0
            if number == wanted:
                rendered = value
                break
            character_offset += len(value)
            previous_logical = logical
        clipped = block.heading_clipped if heading else block.detail_clipped
        last = block.heading_rows - 1 if heading else block.detail_rows - 1
        if clipped and wanted == last:
            rendered = "[… bounded text; expand/inspect source]"
            rendered = next(wrap_lines(rendered, self.text_width))[0]
        self.materialized_rows += 1
        return TimelineRow(
            block.event.ref, rendered, logical if heading else 1_000 + logical,
            window.lane(block.event), heading, character_offset,
        )

    def anchor_row(self, anchor: ViewportAnchor, *, character_offset: int = 0) -> int | None:
        block = self._refs.get(anchor.event)
        if block is None:
            return None
        if anchor.logical_line >= 1_000:
            if not block.detail_rows:
                return block.start
            text, wanted = block.detail_text, anchor.logical_line - 1_000
            base, maximum = block.start + block.heading_rows, block.detail_rows
        else:
            text, wanted = block.heading_text, anchor.logical_line
            base, maximum = block.start, block.heading_rows
        offset, candidate = 0, None
        for index, (line, logical) in enumerate(wrap_lines(text, self.text_width)):
            if index >= maximum:
                break
            if logical == wanted:
                candidate = base + index
                if offset + len(line) > character_offset or not line:
                    return candidate
                offset += len(line)
            elif candidate is not None:
                return candidate
        if candidate is not None:
            return candidate
        return block.start

    def prefix(self, row: TimelineRow, window: TimelineWindow) -> str:
        if self.prefix_width != self._lane_count * 2:
            value = f"[{row.lane}] "
            return value[:self.prefix_width]
        return "".join(
            ("● " if row.heading else "│ ") if lane == row.lane else "· "
            for lane in range(self._lane_count)
        )
