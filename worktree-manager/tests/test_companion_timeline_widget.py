"""Actual Textual viewport, controls, demand and plain-text rendering."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from textual.app import App, ComposeResult
from textual.message_pump import MessagePump
from textual.widgets import Button, Static

from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, HistoryPage, LiveDelta, MAX_STRIP_CACHE_BYTES, SourceToken,
)
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from _companion_timeline_fixtures import event, position, snapshot, token


class TimelineApp(App[None]):
    BINDINGS = [("escape", "quit", "Exit")]

    def __init__(self, window: TimelineWindow) -> None:
        super().__init__()
        self.window = window
        self.requests: list[SubwayTimeline.HistoryRequested] = []
        self.reconciliations: list[SubwayTimeline.ReconciliationRequested] = []
        self.buttons_pressed: list[str | None] = []
        self.changes: list[SubwayTimeline.Changed] = []
        self.demands: list[SubwayTimeline.DemandChanged] = []

    def compose(self) -> ComposeResult:
        yield CompanionTimelinePanel(self.window)

    def on_subway_timeline_history_requested(self, message: SubwayTimeline.HistoryRequested) -> None:
        self.requests.append(message)

    def on_subway_timeline_reconciliation_requested(self, message: SubwayTimeline.ReconciliationRequested) -> None:
        self.reconciliations.append(message)

    def on_button_pressed(self, message: Button.Pressed) -> None:
        self.buttons_pressed.append(message.button.name)

    def on_subway_timeline_changed(self, message: SubwayTimeline.Changed) -> None:
        self.changes.append(message)

    def on_subway_timeline_demand_changed(self, message: SubwayTimeline.DemandChanged) -> None:
        self.demands.append(message)


def window_with_events(count: int) -> tuple[TimelineWindow, SourceToken]:
    source = token()
    window = TimelineWindow()
    window.bind(source)
    window.apply_snapshot(snapshot(source, tuple(event(source, number) for number in range(count))))
    return window, source


def strip_text(timeline: SubwayTimeline, row: int) -> str:
    return "".join(segment.text for segment in timeline.render_line(row))


def test_live_bottom_short_padding_and_sticky_context():
    window, _source = window_with_events(1)
    assert CompanionTimelinePanel._context is MessagePump._context

    async def check():
        app = TimelineApp(window)
        async with app.run_test(size=(80, 32)) as pilot:
            panel = app.query_one(CompanionTimelinePanel)
            timeline = app.query_one(SubwayTimeline)
            await pilot.pause()
            assert panel.is_mounted and timeline.is_mounted
            assert timeline._padding > 0
            assert timeline.virtual_size.height == timeline.size.height
            assert "Recorded event 0" in strip_text(timeline, timeline.size.height - 1)
            assert "Purpose:" in str(app.query_one(".timeline-context", Static).content)
            assert "efforts/active/example/README.md" in str(app.query_one(".timeline-context", Static).content)
            await pilot.press("escape")
        assert not app.is_running

    asyncio.run(check())


def test_pushed_delta_prepend_resize_and_expansion_preserve_browsing_anchor():
    source = token()
    window = TimelineWindow()
    window.bind(source)
    initial = tuple(event(source, number, details="line one\nline two") for number in range(64))
    window.apply_snapshot(snapshot(source, initial))

    async def check():
        app = TimelineApp(window)
        async with app.run_test(size=(90, 30)) as pilot:
            timeline = app.query_one(SubwayTimeline)
            timeline.focus()
            await pilot.pause()
            timeline.scroll_to(y=20, animate=False, immediate=True)
            await pilot.pause()
            assert not window.follow_live
            anchor = timeline.capture_anchor()
            assert anchor is not None
            assert timeline.apply_delta(LiveDelta(
                source, position(source, "live-0"), position(source, "live-1"), (event(source, 64),),
            )).applied
            await pilot.pause()
            retained = timeline.capture_anchor()
            assert retained is not None and retained.event == anchor.event
            assert len(window.unread) == 1
            timeline.action_request_history()
            await pilot.pause()
            assert len(app.requests) == 1 and app.requests[0].token == source
            old_scroll, old_height = timeline.scroll_offset.y, timeline.layout_index.height
            assert timeline.prepend_page(HistoryPage(
                source, position(source, "older-0"), None, (event(source, -2), event(source, -1)),
                Completeness.COMPLETE,
            )).applied
            await pilot.pause()
            retained = timeline.capture_anchor()
            assert retained is not None and retained.event == anchor.event
            assert timeline.scroll_offset.y == old_scroll + timeline.layout_index.height - old_height
            await pilot.resize_terminal(60, 24)
            retained = timeline.capture_anchor()
            assert retained is not None and retained.event == anchor.event
            timeline.selected = anchor.event
            timeline.action_expand_selected()
            timeline.action_expand_selected()
            retained = timeline.capture_anchor()
            assert retained is not None and retained.event == anchor.event
            await pilot.click(".timeline-follow")
            await pilot.pause()
            assert window.follow_live and not window.unread
            assert timeline.is_vertical_scroll_end

    asyncio.run(check())


def test_keyboard_scrollbar_drag_and_history_demand_are_single_flight():
    window, _source = window_with_events(64)

    async def check():
        app = TimelineApp(window)
        async with app.run_test(size=(80, 30)) as pilot:
            timeline = app.query_one(SubwayTimeline)
            timeline.focus()
            await pilot.pause()
            bottom = timeline.scroll_offset.y
            await pilot.press("pageup")
            await pilot.pause()
            assert timeline.scroll_offset.y < bottom and not window.follow_live
            scrollbar = timeline.vertical_scrollbar
            assert scrollbar.region.height > 2
            await pilot.mouse_down(scrollbar, offset=(0, scrollbar.region.height - 2))
            await pilot.hover(scrollbar, offset=(0, 1))
            await pilot.mouse_up(scrollbar, offset=(0, 1))
            await pilot.pause()
            timeline.action_request_history()
            timeline.action_request_history()
            await pilot.pause()
            assert len(app.requests) == 1
            timeline.set_demand_visible(False)
            timeline.action_request_history()
            await pilot.pause()
            assert len(app.requests) == 1
            assert all(state.requested_before is None for state in window.sources.values())

    asyncio.run(check())


def test_render_is_lazy_literal_bounded_and_has_no_io(monkeypatch):
    source = token()
    window = TimelineWindow()
    window.bind(source)
    rows = tuple(event(source, number, summary="[red] **literal** 界e\u0301 \x1b[31m") for number in range(64))
    window.apply_snapshot(snapshot(source, rows))

    async def check():
        app = TimelineApp(window)
        async with app.run_test(size=(50, 28)) as pilot:
            timeline = app.query_one(SubwayTimeline)
            await pilot.pause()
            timeline._clear_cache()
            timeline.layout_index.materialized_rows = 0

            def forbidden(*args, **kwargs):
                raise AssertionError("render attempted I/O")

            def forbidden_lane_count(_window: TimelineWindow) -> int:
                raise AssertionError("render enumerated the window to count actor lanes")

            with monkeypatch.context() as patch:
                patch.setattr("builtins.open", forbidden)
                patch.setattr("socket.create_connection", forbidden)
                patch.setattr("subprocess.Popen", forbidden)
                patch.setattr("os.scandir", forbidden)
                patch.setattr(TimelineWindow, "lane_count", property(forbidden_lane_count))
                for y in range(timeline.size.height):
                    assert timeline.render_line(y).cell_length == timeline.size.width
            assert timeline.layout_index.materialized_rows <= timeline.size.height + 8
            assert timeline.cache_bytes <= MAX_STRIP_CACHE_BYTES
            rendered = "\n".join(strip_text(timeline, y) for y in range(timeline.size.height))
            assert "[red]" in rendered and "**literal**" in rendered
            assert "\x1b" not in rendered
            before = timeline.window.sources[source.identity.source_key].position
            timeline.set_cached_time(datetime(2026, 1, 2, tzinfo=timezone.utc))
            assert timeline.window.sources[source.identity.source_key].position == before

    asyncio.run(check())


def test_token_head_reset_rejects_late_history_and_leaves_unknown_explicit():
    source = token()
    window = TimelineWindow()
    window.bind(source)
    window.apply_snapshot(snapshot(source, (event(source, 1),)))

    async def check():
        app = TimelineApp(window)
        async with app.run_test() as pilot:
            timeline = app.query_one(SubwayTimeline)
            timeline.action_request_history()
            renewed = token(1, head="head-b")
            timeline.bind_source(renewed)
            result = timeline.prepend_page(HistoryPage(
                source, position(source, "older-0"), None, (event(source, 0),), Completeness.COMPLETE,
            ))
            assert not result.applied
            assert not window.events
            await pilot.pause()
            assert "unknown" in str(app.query_one(".timeline-state", Static).content).lower()
            assert "stale-token" in str(app.query_one(".timeline-state", Static).content)
            assert "unknown" in strip_text(timeline, timeline.size.height - 1).lower()

    asyncio.run(check())


@pytest.mark.parametrize("availability", [Availability.HEAD_CHANGED, Availability.UNAVAILABLE])
def test_unobserved_source_clears_sticky_current_context(availability: Availability) -> None:
    window, source = window_with_events(1)

    async def check() -> None:
        app = TimelineApp(window)
        async with app.run_test(size=(80, 24)) as pilot:
            panel = app.query_one(CompanionTimelinePanel)
            await pilot.pause()
            assert "Complete the accepted assignment." in str(
                panel.query_one(".timeline-context", Static).content
            )
            invalidated = replace(
                snapshot(source, ()), availability=availability, position=None, before=None,
            )
            assert panel.apply_snapshot(invalidated).applied
            await pilot.pause()
            content = str(panel.query_one(".timeline-context", Static).content)
            assert "Purpose: unknown" in content and "Effort: unknown" in content
            assert "Complete the accepted assignment." not in content
            assert "efforts/active/example/README.md" not in content
            creation = str(panel.query_one(".timeline-creation", Static).content)
            assert "Creation context unknown." in creation
            assert "Original source and scope" not in creation

    asyncio.run(check())


def test_integrity_gap_retains_rows_and_requests_reconciliation_without_reset():
    source = token()
    window = TimelineWindow(max_events=2, max_proofs=2)
    window.bind(source)
    window.apply_snapshot(snapshot(source, (event(source, 0), event(source, 1))))

    async def check():
        app = TimelineApp(window)
        async with app.run_test(size=(80, 24)) as pilot:
            timeline = app.query_one(SubwayTimeline)
            result = timeline.apply_delta(LiveDelta(
                source, position(source, "live-0"), position(source, "live-1"), (event(source, 2),),
            ))
            assert not result.applied
            await pilot.pause()
            assert len(window.events) == 2
            assert "unavailable" in str(app.query_one(".timeline-state", Static).content)
            panel = app.query_one(CompanionTimelinePanel)
            button = app.query_one(".timeline-reconcile", Button)
            metadata = {
                "button_region": button.region,
                "button_size": button.size,
                "button_disabled": button.disabled,
                "button_display": button.display,
                "panel_region": panel.region,
                "app_size": app.size,
                "screen_region": app.screen.region,
                "toolbar_region": app.query_one(".timeline-buttons").region,
                "controls": {
                    control.name: control.region for control in panel.query(Button)
                },
                "reconciliation_required": window.reconciliation_required,
            }
            print("Reconciliation click geometry:", metadata)
            assert button.display and not button.disabled, metadata
            assert await pilot.click(".timeline-reconcile"), metadata
            await pilot.pause()
            print("Reconciliation click route:", {
                "buttons_pressed": app.buttons_pressed,
                "senders": [type(message._sender).__name__ for message in app.reconciliations],
            })
            assert "reconcile" in app.buttons_pressed
            assert len(app.reconciliations) == 1, {
                **metadata,
                "buttons_pressed": app.buttons_pressed,
                "reconciliation_required_after_click": window.reconciliation_required,
            }
            assert app.reconciliations[0].token == source
            assert app.reconciliations[0].position == position(source, "live-0")
            assert app.reconciliations[0]._sender is timeline
            assert len(window._proofs) == 2 and not app.requests

    asyncio.run(check())


def test_parent_delegates_and_toolbar_messages_keep_child_sender() -> None:
    window, source = window_with_events(1)

    async def check() -> None:
        app = TimelineApp(window)
        async with app.run_test(size=(80, 24)) as pilot:
            panel = app.query_one(CompanionTimelinePanel)
            timeline = app.query_one(SubwayTimeline)
            await pilot.pause()
            panel.call_later(panel.apply_delta, LiveDelta(
                source, position(source, "live-0"), position(source, "live-1"),
                (event(source, 1),),
            ))
            await pilot.pause()
            assert app.changes and app.changes[-1].result is not None
            assert app.changes[-1].result.applied
            assert app.changes[-1]._sender is timeline
            app.changes.clear()
            assert await pilot.click(".timeline-follow")
            await pilot.pause()
            assert app.changes and all(message._sender is timeline for message in app.changes)
            assert await pilot.click(".timeline-older")
            await pilot.pause()
            assert len(app.requests) == 1
            assert app.requests[0].token == source and app.requests[0]._sender is timeline
            panel.call_later(panel.set_demand_visible, False)
            await pilot.pause()
            assert app.demands and app.demands[-1].visible is False
            assert app.demands[-1]._sender is timeline

    asyncio.run(check())
