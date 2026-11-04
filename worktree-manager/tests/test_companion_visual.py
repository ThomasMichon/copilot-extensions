"""Opt-in SVG comparisons of the production timeline with synthetic owner data."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import re
from collections.abc import Callable

import pytest
from textual.app import App, ComposeResult

from worktree_manager.companion_timeline_contract import LiveDelta, Provider, TimelineKind
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from _companion_timeline_fixtures import event, position, snapshot, token


BASELINES = Path(__file__).with_name("visual_baselines")


class VisualApp(App[None]):
    TITLE = "Sidecar timeline - synthetic rendered fixture"

    def __init__(self, window: TimelineWindow) -> None:
        super().__init__()
        self.window = window
        self.theme = "textual-dark"

    def compose(self) -> ComposeResult:
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


@pytest.mark.parametrize("state", ["live", "browsing"])
def test_timeline_visual(
    state: str, record_property: Callable[[str, object], None],
) -> None:
    async def capture() -> str:
        window = visual_window()
        app = VisualApp(window)
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
            return offline_svg(app.export_screenshot())

    svg = asyncio.run(capture())
    record_property("sidecar-state", state)
    record_property("sidecar-svg", svg)
    baseline = BASELINES / f"timeline-{state}.svg"
    assert baseline.is_file(), f"Review the JUnit sidecar-svg capture before adding {baseline.name}"
    assert svg == baseline.read_text(encoding="utf-8")
