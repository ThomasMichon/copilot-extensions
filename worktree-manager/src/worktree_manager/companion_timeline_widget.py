"""Public Textual Line-API timeline with bounded strips and explicit demand messages."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from collections.abc import Callable
from datetime import datetime
from itertools import islice

from rich.segment import Segment
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Button, Static

from .companion_timeline_contract import (
    Availability, EventRef, HistoryPage, LiveDelta, MAX_STRIP_CACHE_BYTES, OVERSCAN_LINES,
    SourcePosition, SourceToken, TimelineSnapshot, ViewportAnchor, aware_time,
)
from .companion_timeline_layout import TimelineLayout, display_text, wrap_lines
from .companion_timeline_model import TimelineWindow, WindowResult


class SubwayTimeline(ScrollView, can_focus=True):
    DEFAULT_CSS = """
    SubwayTimeline { height: 1fr; overflow-x: hidden; overflow-y: auto; }
    """
    BINDINGS = [
        ("end", "follow_live", "Follow live"),
        ("home", "request_history", "Older"),
        ("enter", "expand_selected", "Details"),
    ]

    class Changed(Message):
        def __init__(self, result: WindowResult | None = None) -> None:
            super().__init__()
            self.result = result

    class HistoryRequested(Message):
        def __init__(self, token: SourceToken, before: SourcePosition) -> None:
            super().__init__()
            self.token, self.before = token, before

    class DemandChanged(Message):
        def __init__(self, visible: bool) -> None:
            super().__init__()
            self.visible = visible

    class ReconciliationRequested(Message):
        def __init__(self, token: SourceToken, position: SourcePosition | None) -> None:
            super().__init__()
            self.token, self.position = token, position

    def __init__(
        self, window: TimelineWindow | None = None, *, name: str | None = None,
        id: str | None = None, classes: str | None = None, disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.window = window if window is not None else TimelineWindow()
        self.layout_index = TimelineLayout()
        self.selected: EventRef | None = None
        self.demand_visible = True
        self._clock: datetime | None = None
        self._programmatic = False
        self._padding = 0
        self._layout_generation = 0
        self._anchor_character_offset = 0
        self._cache: OrderedDict[tuple[int, int, int], tuple[Strip, int]] = OrderedDict()
        self.cache_bytes = 0

    def on_mount(self) -> None:
        self._reflow(None)

    def on_resize(self, event: events.Resize) -> None:
        self._reflow(self.capture_anchor())

    def notify_style_update(self) -> None:
        super().notify_style_update()
        self._clear_cache()

    def _clear_cache(self) -> None:
        self._cache.clear()
        self.cache_bytes = 0

    def capture_anchor(self) -> ViewportAnchor | None:
        for screen_row in range(max(0, self.size.height)):
            row = self.layout_index.row(int(self.scroll_offset.y) + screen_row - self._padding, self.window)
            if row is not None:
                self._anchor_character_offset = row.character_offset
                return ViewportAnchor(row.event, row.logical_line, screen_row)
        return None

    def _reflow(self, anchor: ViewportAnchor | None) -> None:
        self.layout_index.rebuild(self.window, self.size.width or 1, now=self._clock)
        self._padding = max(0, self.size.height - self.layout_index.height)
        self.virtual_size = Size(max(1, self.size.width), max(self.size.height, self.layout_index.height))
        self._layout_generation += 1
        self._clear_cache()
        if self.selected is None or self.window.event(self.selected) is None:
            self.selected = self.window.events[-1].ref if self.window.events else None
        self._programmatic = True
        try:
            if self.window.follow_live:
                self.scroll_end(animate=False, immediate=True, x_axis=False)
            elif anchor is not None:
                row = self.layout_index.anchor_row(anchor, character_offset=self._anchor_character_offset)
                if row is not None:
                    self.scroll_to(y=max(0, row + self._padding - anchor.screen_row), animate=False, immediate=True)
        finally:
            self._programmatic = False
        self.refresh()

    def _change(self, apply: Callable[[], WindowResult]) -> WindowResult:
        anchor = self.capture_anchor()
        self.window.pin_viewport(
            anchor.event if anchor is not None and not self.window.follow_live else None,
        )
        result = apply()
        self._reflow(anchor)
        self._post_timeline_message(self.Changed(result))
        return result

    def _post_timeline_message(self, message: Message) -> bool:
        # Parent delegates may be the active message pump when constructing events.
        return self.post_message(message.set_sender(self))

    def bind_source(self, token: SourceToken) -> WindowResult:
        return self._change(lambda: self.window.bind(token))

    def apply_snapshot(self, snapshot: TimelineSnapshot) -> WindowResult:
        return self._change(lambda: self.window.apply_snapshot(snapshot))

    def apply_delta(self, delta: LiveDelta) -> WindowResult:
        return self._change(lambda: self.window.apply_delta(delta))

    def prepend_page(self, page: HistoryPage) -> WindowResult:
        return self._change(lambda: self.window.prepend_page(page))

    def set_cached_time(self, now: datetime) -> None:
        """Update labels from a caller-owned cached clock, never from source polling."""
        aware_time(now, "cached timeline time")
        self._clock = now
        self._reflow(self.capture_anchor())

    def set_demand_visible(self, visible: bool) -> None:
        self.demand_visible = visible
        if not visible:
            self.window.sources = {
                key: replace(state, requested_before=None) for key, state in self.window.sources.items()
            }
        self._post_timeline_message(self.DemandChanged(visible))

    def on_unmount(self) -> None:
        self.set_demand_visible(False)
        self._clear_cache()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if not self._programmatic and old_value != new_value:
            self.window.set_follow_live(False)
            self._post_timeline_message(self.Changed())
            if new_value <= OVERSCAN_LINES and self.demand_visible:
                self.action_request_history()

    def action_follow_live(self) -> None:
        self.window.set_follow_live(True)
        self._programmatic = True
        try:
            self.scroll_end(animate=False, immediate=True, x_axis=False)
        finally:
            self._programmatic = False
        self._post_timeline_message(self.Changed())

    def action_request_history(self) -> None:
        if not self.demand_visible:
            return
        for state in self.window.sources.values():
            if (
                state.availability is Availability.OBSERVED and state.before is not None
                and state.requested_before is None
            ):
                result = self.window.request_history(state.token, state.before)
                if result.applied:
                    self._post_timeline_message(self.HistoryRequested(state.token, state.before))
                else:
                    self._post_timeline_message(self.Changed(result))
                return

    def action_reconcile_source(self) -> None:
        for key in sorted(self.window.reconciliation_required):
            state = self.window.sources[key]
            self._post_timeline_message(self.ReconciliationRequested(state.token, state.position))

    def action_expand_selected(self) -> None:
        if self.selected is not None:
            self._change(lambda: self.window.toggle_expansion(self.selected))

    def on_click(self, event: events.Click) -> None:
        row = self.layout_index.row(
            int(self.scroll_offset.y) + event.offset.y - self._padding, self.window,
        )
        if row is not None:
            self.selected = row.event
            if row.heading and event.offset.x < self.layout_index.prefix_width:
                self.action_expand_selected()

    def _store(self, key: tuple[int, int, int], strip: Strip) -> Strip:
        size = 64 + sum(64 + len(segment.text.encode("utf-8")) for segment in strip)
        if size <= MAX_STRIP_CACHE_BYTES:
            self._cache[key] = strip, size
            self.cache_bytes += size
            while self.cache_bytes > MAX_STRIP_CACHE_BYTES or len(self._cache) > 2048:
                _old_key, (_old_strip, old_size) = self._cache.popitem(last=False)
                self.cache_bytes -= old_size
        return strip

    def render_line(self, y: int) -> Strip:
        index = int(self.scroll_offset.y) + y
        key = self._layout_generation, index, self.size.width
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached[0]
        width = max(1, self.size.width)
        row = self.layout_index.row(index - self._padding, self.window)
        if row is None:
            if not self.window.events and y == max(0, self.size.height - 1):
                observed = bool(self.window.sources) and all(
                    state.availability is Availability.OBSERVED for state in self.window.sources.values()
                )
                label = "No events in the observed window." if observed else "History unknown or unavailable."
                strip = Strip([Segment(label, self.rich_style)]).crop_extend(0, width, self.rich_style)
                return self._store(key, strip)
            return Strip.blank(width, self.rich_style)
        prefix = self.layout_index.prefix(row, self.window)
        strip = Strip([
            Segment(prefix, self.rich_style), Segment(row.text, self.rich_style),
        ]).crop_extend(0, width, self.rich_style)
        return self._store(key, strip)


class CompanionTimelinePanel(Vertical):
    """Sticky source context and explicit controls around the virtual canvas."""

    DEFAULT_CSS = """
    CompanionTimelinePanel { height: 1fr; }
    CompanionTimelinePanel .timeline-context { height: auto; max-height: 3; }
    CompanionTimelinePanel .timeline-state { height: auto; max-height: 2; }
    CompanionTimelinePanel .timeline-creation { height: auto; max-height: 8; }
    CompanionTimelinePanel .timeline-buttons { height: 3; }
    """

    def __init__(
        self, window: TimelineWindow | None = None, *, name: str | None = None,
        id: str | None = None, classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.window = window if window is not None else TimelineWindow()
        self.creation_expanded = False

    def compose(self) -> ComposeResult:
        yield Static(classes="timeline-context")
        yield Static(classes="timeline-state")
        yield Static(classes="timeline-creation")
        yield SubwayTimeline(self.window)
        with Horizontal(classes="timeline-buttons"):
            yield Button("Follow live", name="follow", classes="timeline-follow")
            yield Button("Older", name="older", classes="timeline-older", disabled=True)
            yield Button("Creation context", name="creation")
            yield Button("Reconcile source", name="reconcile", classes="timeline-reconcile", disabled=True)
            yield Static(classes="timeline-unread")

    def on_mount(self) -> None:
        self._update_context()

    def bind_source(self, token: SourceToken) -> WindowResult:
        return self.query_one(SubwayTimeline).bind_source(token)

    def apply_snapshot(self, snapshot: TimelineSnapshot) -> WindowResult:
        return self.query_one(SubwayTimeline).apply_snapshot(snapshot)

    def apply_delta(self, delta: LiveDelta) -> WindowResult:
        return self.query_one(SubwayTimeline).apply_delta(delta)

    def prepend_page(self, page: HistoryPage) -> WindowResult:
        return self.query_one(SubwayTimeline).prepend_page(page)

    def set_cached_time(self, now: datetime) -> None:
        self.query_one(SubwayTimeline).set_cached_time(now)

    def set_demand_visible(self, visible: bool) -> None:
        self.query_one(SubwayTimeline).set_demand_visible(visible)

    def _update_context(self) -> None:
        purpose, effort, creation = self.window.context()
        heading = Text("Purpose: " + display_text((purpose or "unknown")[:240]))
        heading.append("\nEffort: " + display_text(effort or "unknown"))
        self.query_one(".timeline-context", Static).update(heading)
        status = "; ".join(
            f"{key[:40]}: {state.availability.value}/{state.completeness.value}"
            for key, state in self.window.sources.items()
        ) or "No source bound; history unknown."
        if not self.window.last_result.applied:
            status += f"\n{self.window.last_result.code.value}: {self.window.last_result.reason}"
        if self.window.evicted:
            status += f"; {self.window.evicted} display rows evicted; source history not erased"
        self.query_one(".timeline-state", Static).update(Text(display_text(status)))
        context = self.query_one(".timeline-creation", Static)
        context.display = self.creation_expanded
        context.update(Text("\n".join(
            line for line, _logical in islice(wrap_lines(creation or "Creation context unknown.", max(1, self.size.width)), 8)
        )))
        self.query_one(".timeline-unread", Static).update(Text(f"{len(self.window.unread)} unread"))
        self.query_one(".timeline-older", Button).disabled = not any(
            state.availability is Availability.OBSERVED and state.before is not None
            for state in self.window.sources.values()
        )
        reconcile = self.query_one(".timeline-reconcile", Button)
        reconcile.display = bool(self.window.reconciliation_required)
        reconcile.disabled = not self.window.reconciliation_required

    def on_subway_timeline_changed(self, message: SubwayTimeline.Changed) -> None:
        self._update_context()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        timeline = self.query_one(SubwayTimeline)
        if event.button.name == "follow":
            timeline.action_follow_live()
        elif event.button.name == "older":
            timeline.action_request_history()
        elif event.button.name == "creation":
            self.creation_expanded = not self.creation_expanded
            self._update_context()
        elif event.button.name == "reconcile":
            timeline.action_reconcile_source()
