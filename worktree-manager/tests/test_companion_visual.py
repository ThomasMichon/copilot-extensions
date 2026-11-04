"""Opt-in SVG comparisons of the production timeline with synthetic owner data."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import re
from collections.abc import Callable

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Select, Static

from worktree_manager.companion_timeline_contract import (
    ActorKind, Durability, LiveDelta, Provider, TimelineKind,
)
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from worktree_manager.companion_summary import bridge_status
from _companion_timeline_fixtures import event, position, snapshot, token


BASELINES = Path(__file__).with_name("visual_baselines")


class VisualApp(App[None]):
    TITLE = "Sidecar timeline - synthetic rendered fixture"

    def __init__(self, window: TimelineWindow, *, session: bool = False) -> None:
        super().__init__()
        self.window = window
        self.session = session
        self.theme = "textual-dark"

    def compose(self) -> ComposeResult:
        if self.session:
            yield Static(bridge_status(
                self.window, "selected session: partial retained events; answerability unknown; membership discovery paused",
            ))
            yield Select([
                ("Bridge feed: membership (discover delegates)", "membership"),
                ("Session: helper-example (owned; reported link)", "session"),
            ], value="session", allow_blank=False)
        yield CompanionTimelinePanel(self.window)


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


def offline_svg(svg: str) -> str:
    # Keep every rendered cell, style and coordinate; omit only remote font loading.
    svg = re.sub(r"    @font-face \{.*?\n    \}\n", "", svg, flags=re.DOTALL)
    return re.sub(r"(?m)^[ \t]+$", "", svg)


@pytest.mark.parametrize("state", ["live", "browsing", "session"])
def test_timeline_visual(
    state: str, record_property: Callable[[str, object], None],
) -> None:
    async def capture() -> str:
        window = visual_window()
        if state == "session":
            membership = window.sources["bridge"].token
            assert not window.source_error(membership, "Reader paused; retained evidence only.").applied
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
        app = VisualApp(window, session=state == "session")
        async with app.run_test(size=(100, 32)) as pilot:
            timeline = app.query_one(SubwayTimeline)
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
                if state == "session":
                    await pilot.press("end", "enter")
                    await pilot.pause()
            return offline_svg(app.export_screenshot())

    svg = asyncio.run(capture())
    record_property("sidecar-state", state)
    record_property("sidecar-svg", svg)
    baseline = BASELINES / f"timeline-{state}.svg"
    assert baseline.is_file(), f"Review the JUnit sidecar-svg capture before adding {baseline.name}"
    assert svg == baseline.read_text(encoding="utf-8")
