"""Height-indexed literal timeline rows; no widgets, strips or source I/O."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, tzinfo

from rich.cells import cell_len

from .companion_timeline_contract import (
    EventRef, MAX_DETAIL_LINES, MAX_EXPANDED_GROUPS, MAX_LANES, MAX_PAGE_EVENTS,
    OVERSCAN_LINES, Provider, TimelineEvent, TimelineKind, ViewportAnchor, literal_display,
)
from .companion_timeline_model import ResultCode, TimelineWindow, WindowResult

OUTPUT_GROUP_LINE = 1_000_000


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
    members: tuple[EventRef, ...] = ()


def output_runs(events: tuple[TimelineEvent, ...]) -> Iterator[tuple[TimelineEvent, ...]]:
    """Partition only typed adjacent output, within a single source and reported actor."""
    run: list[TimelineEvent] = []
    for event in events:
        output = (
            event.source.provider is Provider.BRIDGE and event.kind is TimelineKind.SOURCE_OUTPUT
            and event.actor.actor_id is not None
        )
        if run and (
            not output or event.source != run[0].source or event.actor != run[0].actor
            or len(run) == MAX_PAGE_EVENTS
        ):
            yield tuple(run)
            run = []
        if output:
            run.append(event)
        else:
            yield (event,)
    if run:
        yield tuple(run)


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
        self._groups: dict[EventRef, EventBlock] = {}
        self._group_aliases: dict[EventRef, EventBlock] = {}
        self._expanded_output: frozenset[EventRef] = frozenset()
        self._expanded_output_count = 0
        self.materialized_rows = 0
        self.timezone: tzinfo | None = None

    def _local_time(self, event: TimelineEvent) -> datetime | None:
        stamp = event.occurred_at or event.observed_at
        if stamp is None:
            return None
        try:
            return stamp.astimezone(self.timezone)
        except (OverflowError, OSError, ValueError):
            return None

    def _time(self, event: TimelineEvent, now: datetime | None, *, include_date: bool = False) -> str:
        local = self._local_time(event)
        if local is None:
            return "--:--"
        stamp = event.occurred_at or event.observed_at
        qualifier = "" if event.occurred_at is not None else "~"
        value = qualifier + local.strftime("%Y-%m-%d %H:%M" if include_date else "%H:%M")
        if now is not None and stamp is not None:
            age = max(0, int((now - stamp).total_seconds()))
            value += f" ({age // 60}m)"
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
            f"Time: {event.occurred_at or event.observed_at or 'unknown'}"
            + (" (observed, not occurrence)" if event.occurred_at is None else ""),
            f"Kind: {event.kind.value}; actor: {event.actor.label}",
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

    def rebuild(
        self, window: TimelineWindow, width: int, *, now: datetime | None = None,
        anchors: tuple[EventRef, ...] = (),
    ) -> None:
        previous_width = self.text_width
        self.width = max(1, width)
        self._lane_count = window.lane_count
        wide_prefix = self._lane_count * 2
        self.prefix_width = wide_prefix if self.width >= wide_prefix + 12 else min(6, self.width - 1)
        self.text_width = max(1, self.width - self.prefix_width)
        blocks = []
        start = 0
        previous_day = None
        groups: dict[EventRef, EventBlock] = {}
        expanded_output: set[EventRef] = set()
        projected: list[tuple[TimelineEvent, tuple[EventRef, ...], bool]] = []
        expanded_count = 0
        for run in output_runs(window.events):
            output = run[0].source.provider is Provider.BRIDGE and run[0].kind is TimelineKind.SOURCE_OUTPUT
            if output and run[0].actor.actor_id is not None:
                members = tuple(event.ref for event in run)
                expanded = bool(self._expanded_output.intersection(members)) and expanded_count < MAX_EXPANDED_GROUPS
                projected.append((run[0], members, expanded))
                if expanded:
                    expanded_count += 1
                    expanded_output.update(members)
                    projected.extend((event, (), False) for event in run)
            else:
                projected.append((run[0], (), False))
        self._expanded_output = frozenset(expanded_output)
        self._expanded_output_count = expanded_count
        for event, members, expanded in projected:
            previous = self._refs.get(event.ref) if previous_width == self.text_width else None
            stamp = event.occurred_at or event.observed_at
            local = self._local_time(event)
            day = local.date() if local is not None else None
            heading = (
                f"{self._time(event, now, include_date=day != previous_day)}  {event.summary}"
            )
            if members:
                heading = (
                    f"{self._time(event, now, include_date=day != previous_day)}  "
                    f"{'[-]' if expanded else '[+]'} Output: {len(members)} loaded records"
                )
                previous = self._groups.get(event.ref) if previous_width == self.text_width else None
            previous_day = day
            if window.lane(event) == MAX_LANES - 1:
                heading = "Other actors (overflow lane) | " + heading
            heading_rows, heading_clipped = (
                (previous.heading_rows, previous.heading_clipped)
                if previous is not None and previous.heading_text == heading
                else self._count(heading, self.text_width, OVERSCAN_LINES)
            )
            details = self._details(event, window) if not members and event.ref in window.expanded else ""
            if details and stamp is not None and local is None:
                details = "Local clock unavailable; original source time follows.\n" + details
            detail_rows, detail_clipped = (
                (previous.detail_rows, previous.detail_clipped)
                if previous is not None and previous.detail_text == details
                else self._count(details, self.text_width, MAX_DETAIL_LINES) if details else (0, False)
            )
            height = heading_rows + detail_rows
            block = EventBlock(
                event, start, height, heading_rows, detail_rows, heading, details,
                heading_clipped, detail_clipped,
                members,
            )
            blocks.append(block)
            for ref in members:
                groups[ref] = block
            start += height
        self.blocks, self.height = tuple(blocks), start
        self._starts = tuple(block.start for block in blocks)
        aliases: dict[EventRef, EventBlock] = {}
        for ref in anchors[:2]:
            previous_group = self._output_group(ref)
            if ref not in groups and previous_group is not None:
                survivor = next((groups[member] for member in previous_group.members if member in groups), None)
                if survivor is not None:
                    aliases[ref] = survivor
        self._group_aliases = aliases
        self._groups = groups
        self._refs = {
            ref: block for block in blocks for ref in (block.members or (block.event.ref,))
        }
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
            block.event.ref, rendered, OUTPUT_GROUP_LINE if block.members else logical if heading else 1_000 + logical,
            window.lane(block.event), heading, character_offset,
        )

    def anchor_row(self, anchor: ViewportAnchor, *, character_offset: int = 0) -> int | None:
        block = self._output_group(anchor.event) if anchor.logical_line == OUTPUT_GROUP_LINE else self._refs.get(anchor.event)
        if block is None:
            block = self._group_aliases.get(anchor.event)
        if block is None:
            return None
        if block.members:
            text, wanted = block.heading_text, 0
            base, maximum = block.start, block.heading_rows
        elif anchor.logical_line >= 1_000:
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

    def _output_group(self, ref: EventRef) -> EventBlock | None:
        return self._groups.get(ref) or self._group_aliases.get(ref)

    def visible_ref(self, ref: EventRef, *, group: bool = False) -> EventRef:
        block = self._output_group(ref) if group else self._refs.get(ref)
        if block is None:
            block = self._group_aliases.get(ref)
        return block.event.ref if block is not None else ref

    def is_group(self, ref: EventRef) -> bool:
        block = self._refs.get(ref)
        return bool(block is not None and block.members)

    def has_group(self, ref: EventRef) -> bool:
        return self._output_group(ref) is not None

    def toggle_output(self, ref: EventRef) -> WindowResult | None:
        group = self._output_group(ref)
        if group is None:
            return None
        members = frozenset(group.members)
        if self._expanded_output.intersection(members):
            self._expanded_output -= members
        elif self._expanded_output_count >= MAX_EXPANDED_GROUPS:
            return WindowResult(False, ResultCode.LIMIT, "Expanded output-group budget exhausted; collapse a group to continue.")
        else:
            self._expanded_output |= members
        return WindowResult(True, ResultCode.APPLIED, "Loaded output group toggled; source history unchanged.")

    def is_visible(self, ref: EventRef, top: int, height: int, *, group: bool = False) -> bool:
        block = self._output_group(ref) if group else self._refs.get(ref)
        return (
            block is not None and height > 0
            and block.start < top + height and block.start + block.height > top
        )

    def prefix(self, row: TimelineRow, window: TimelineWindow) -> str:
        if self.prefix_width != self._lane_count * 2:
            value = f"[{row.lane}] "
            return value[:self.prefix_width]
        return "".join(
            ("● " if row.heading else "│ ") if lane == row.lane else "  "
            for lane in range(self._lane_count)
        )
