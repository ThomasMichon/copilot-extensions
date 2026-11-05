"""Opt-in SVG comparisons of the production timeline with synthetic owner data."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timezone
from pathlib import Path
import re
from collections.abc import Callable

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual import events
from textual.widgets import Collapsible, Select, Static, TabbedContent

from worktree_manager.companion_timeline_contract import (
    ActorKind, Durability, LiveDelta, Provider, TimelineKind,
)
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from worktree_manager.companion_summary import bridge_status, current_input_text
from worktree_manager.mux_companion import MuxCompanionApp, _CompanionData
from worktree_manager.companion_bridge_current_requests import CurrentRequestState, decode_current_requests
from _companion_timeline_fixtures import event, position, snapshot, token


BASELINES = Path(__file__).with_name("visual_baselines")


class VisualApp(App[None]):
    TITLE = "Sidecar timeline - synthetic rendered fixture"

    def __init__(
        self, window: TimelineWindow, *, session: bool = False, current: CurrentRequestState | None = None,
        bridge_message: str | None = None,
    ) -> None:
        super().__init__()
        self.window = window
        self.session = session
        self.current = current
        self.bridge_message = bridge_message
        self.theme = "textual-dark"

    def compose(self) -> ComposeResult:
        yield CompanionTimelinePanel(self.window, source_controls=self.session)

    def on_mount(self) -> None:
        if self.session:
            self.query_one("#timeline-bridge-status", Static).update(bridge_status(
                self.window, self.bridge_message or
                "selected session: partial retained events; answerability unknown; membership discovery paused",
            ))
            control = self.query_one(Select)
            control.set_options([
                ("Bridge feed: membership (discover delegates)", "membership"),
                ("Session: helper-example (owned; reported link)", "session"),
            ])
            control.value = "session"
        if self.current is not None:
            self.query_one("#timeline-current-input", Static).update(Text(self.current.summary()))
            self.query_one("#timeline-current-compact", Static).update(current_input_text(self.current))


def visual_window() -> TimelineWindow:
    window = TimelineWindow()
    for provider, kind, summary, actor in (
        (Provider.WORKTREES, TimelineKind.PURPOSE_CHANGED,
         "Implement the accepted sample assignment", "session-example"),
        (Provider.AGENDA, TimelineKind.AGENDA_TRANSITION,
         "Validating the sample implementation", "session-example"),
        (Provider.BRIDGE, TimelineKind.BRIDGE_MEMBER_CHANGED,
         "Known helper reported a bounded contribution", "helper-example"),
    ):
        source = token(key=provider.value)
        source = replace(source, identity=replace(source.identity, provider=provider))
        assert window.bind(source).applied
        rows = tuple(replace(
            event(source, number, actor=actor, summary=summary),
            kind=kind,
        ) for number in range(0, 6, 3))
        assert window.apply_snapshot(replace(
            snapshot(source, rows),
            purpose="Build and validate the sample assignment.",
            creation_context="Synthetic owner observations; no installed runtime or live session.",
        )).applied
    return window


def output_visual_window() -> TimelineWindow:
    window = TimelineWindow()
    for provider, number, summary, kind in (
        (Provider.WORKTREES, 0, "Build and validate the sample assignment", TimelineKind.PURPOSE_CHANGED),
        (Provider.AGENDA, 1, "Validate the accepted implementation", TimelineKind.AGENDA_TRANSITION),
        (Provider.BRIDGE, 2, "Tool started: Inspect source", TimelineKind.SOURCE_EVENT),
    ):
        source = token(key=provider.value)
        source = replace(source, identity=replace(source.identity, provider=provider))
        rows = [replace(event(source, number, summary=summary), kind=kind)]
        if provider is Provider.BRIDGE:
            rows.extend(replace(
                event(source, n, summary="Message output"), kind=TimelineKind.SOURCE_OUTPUT,
            ) for n in range(3, 23))
            second = replace(
                event(source, 23, actor="helper-example", summary="Reported agent start: Compare results"),
                kind=TimelineKind.CONTRIBUTION_REPORTED,
            )
            second = replace(second, actor=replace(second.actor, kind=ActorKind.SUBAGENT))
            rows.append(second)
            rows.extend(replace(
                event(source, n, actor="helper-example", summary="Thought output"),
                actor=second.actor, kind=TimelineKind.SOURCE_OUTPUT,
            ) for n in range(24, 44))
            rows.extend((
                replace(event(source, 44, summary="Tool completed: Verify results"), actor=second.actor),
                replace(event(source, 45, summary="Bridge session: future_output"), actor=second.actor),
            ))
        assert window.bind(source).applied
        assert window.apply_snapshot(snapshot(source, tuple(rows))).applied
    return window


def offline_svg(svg: str) -> str:
    # Keep every rendered cell, style and coordinate; omit only remote font loading.
    svg = re.sub(r"    @font-face \{.*?\n    \}\n", "", svg, flags=re.DOTALL)
    return re.sub(r"(?m)^[ \t]+$", "", svg)


def current_fixture(state: str) -> CurrentRequestState:
    body = {
        "version": 1, "client_incarnation": "a" * 32, "revision": 1,
        "availability": "observed", "completeness": "complete", "reason": "local-acp-waiters",
        "observed_at": "2026-11-04T00:00:00+00:00",
        "requests": [{"occurrence_id": "b" * 32, "kind": "input"}] if state == "current-pending" else [],
    }
    if state == "current-partial":
        body.update(completeness="partial", reason="waiter-coverage")
    elif state == "current-unknown":
        body.update(availability="unknown", completeness="unknown", reason="client-disconnected")
    return decode_current_requests(body)


@pytest.mark.parametrize("state", [
    "live", "browsing", "session", "session-gap",
    "current-pending", "current-cleared", "current-partial", "current-unknown",
])
def test_timeline_visual(
    state: str, record_property: Callable[[str, object], None],
) -> None:
    async def capture() -> str:
        window = visual_window()
        gap_message = "Timeline history error: retention-gap: position lies outside the retained event range"
        session = state in {"session", "session-gap"} or state.startswith("current-")
        if session:
            membership = window.sources["bridge"].token
            assert window.pause_source(membership).applied
            selected = replace(membership, identity=replace(membership.identity, source_key="bridge-session-example"))
            assert window.bind(selected).applied
            rows = (
                replace(event(selected, 8, actor="helper-example", summary="Bridge session: ask_user_request"),
                        kind=TimelineKind.INPUT_REQUEST, durability=Durability.UNKNOWN,
                        details="Historical request occurrence; current answerability unknown. No answer action."),
                replace(event(selected, 9, actor="helper-example", summary="Bridge session: compaction_start"),
                        kind=TimelineKind.COMPACTION, durability=Durability.UNKNOWN,
                        details="Pre-context classification unknown; active pre-window capacity is unqualified."),
            )
            rows = tuple(replace(row, actor=replace(row.actor, kind=ActorKind.BRIDGE_SESSION)) for row in rows)
            assert window.apply_snapshot(snapshot(selected, rows)).applied
        app = VisualApp(
            window, session=session, current=current_fixture(state) if state.startswith("current-") else None,
            bridge_message=gap_message if state == "session-gap" else None,
        )
        async with app.run_test(size=(100, 32)) as pilot:
            timeline = app.query_one(SubwayTimeline)
            timeline.layout_index.timezone = timezone.utc
            timeline._reflow(timeline.capture_anchor())
            timeline.focus()
            await pilot.pause()
            if state == "browsing":
                await pilot.press("home", "enter")
                await pilot.pause()
                anchor = timeline.capture_anchor()
                assert anchor is not None
                source = window.sources["bridge"].token
                assert timeline.apply_delta(LiveDelta(
                    source, position(source, "live-0"), position(source, "live-1"),
                    (event(source, 7, actor="helper-example", summary="Additional helper observation"),),
                )).applied
                await pilot.pause()
                assert not window.follow_live and len(window.unread) == 1
                assert timeline.capture_anchor().event == anchor.event
            else:
                assert window.follow_live and timeline.is_vertical_scroll_end
                if session:
                    await pilot.press("end", "enter")
                    await pilot.pause()
                if state == "session-gap":
                    assert not timeline.source_error(
                        selected, gap_message,
                    ).applied
                    app.query_one(Collapsible).collapsed = False
                    await pilot.pause()
            return offline_svg(app.export_screenshot())

    svg = asyncio.run(capture())
    record_property("sidecar-state", state)
    record_property("sidecar-svg", svg)
    baseline = BASELINES / f"timeline-{state}.svg"
    assert baseline.is_file(), f"Review the JUnit sidecar-svg capture before adding {baseline.name}"
    assert svg == baseline.read_text(encoding="utf-8")


class FullVisualApp(MuxCompanionApp):
    """Production chrome with fixed synthetic observations, not an owner integration."""

    output_fixture = False

    def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()
        self._timeline_attachment.begin_close()
        self._data = _CompanionData(
            worktree={"id": "sample-work", "repo": "example", "branch": "worktree/sample", "state": "active"},
            attention={"availability": "observed", "rest": "busy", "session_id": "sample-head",
                       "reported_at": None}, attention_error=None,
        )
        self._loading = False
        self._refresh_view(reload=False)
        self._timeline_panel.replace_window(output_visual_window() if self.output_fixture else visual_window())
        self.query_one("#timeline-agenda-status", Static).update("Rich Agenda: fixed synthetic operations")
        self.query_one("#timeline-bridge-status", Static).update("Bridge: fixed synthetic membership evidence")
        self.query_one("#views", TabbedContent).active = "timeline-tab"


@pytest.mark.parametrize("state,size", [
    ("full", (110, 40)), ("narrow", (60, 24)), ("fault", (60, 24)), ("evidence", (110, 40)),
    ("output", (110, 40)), ("output-narrow", (60, 24)),
])
def test_full_companion_visual(
    state: str, size: tuple[int, int], record_property: Callable[[str, object], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("fixed presentation fixture attempted an owner read")

    monkeypatch.setattr("worktree_manager.mux_companion._load_current_worktree", forbidden)
    monkeypatch.setattr("worktree_manager.engine_client.run_json", forbidden)

    async def capture() -> str:
        app = FullVisualApp()
        app.output_fixture = state.startswith("output")
        app.theme = "textual-dark"
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            timeline = app.query_one(SubwayTimeline)
            timeline.layout_index.timezone = timezone.utc
            timeline._reflow(timeline.capture_anchor())
            timeline.focus()
            if state == "fault":
                app._timeline_panel.provider_notice(Provider.WORKTREES, "Provider capability unavailable", failed=True)
                app._action_message = Text("View switch failed: exact fixture failure")
                app._refresh_view(reload=False)
            if state == "evidence":
                await pilot.click("CollapsibleTitle")
            await pilot.pause()
            assert timeline.window.follow_live
            assert not app.query_one("#status").display
            assert timeline.size.height >= (10 if state == "fault" else 12 if state == "evidence" else size[1] // 2)
            assert app.query_one(Collapsible).collapsed == (state != "evidence")
            if state == "fault":
                assert app.query_one("#timeline-action").region.height > 0
                assert "Worktrees: error" in str(app.query_one(".timeline-health", Static).content)
            assert app.screen.scroll_offset.y == 0
            assert app.query_one("#footer-row").region.bottom <= size[1] - 1
            assert not calls
            if app.output_fixture:
                assert len(timeline.window.events) == 46 and len(timeline.layout_index.blocks) == 8
                assert [len(block.members) for block in timeline.layout_index.blocks if block.members] == [20, 20]
            return offline_svg(app.export_screenshot())

    svg = asyncio.run(capture())
    record_property("sidecar-state", "companion-" + state)
    record_property("sidecar-svg", svg)
    baseline = BASELINES / f"companion-{state}.svg"
    assert baseline.is_file(), f"Review the JUnit sidecar-svg capture before adding {baseline.name}"
    assert svg == baseline.read_text(encoding="utf-8")
