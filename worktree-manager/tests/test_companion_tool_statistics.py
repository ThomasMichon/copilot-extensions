"""Owner statistics survive paging and bounded UI delivery, without a second reader."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os

import pytest
from textual.widgets import Select, Static, TabbedContent

from worktree_manager import companion_bridge_source as bridge
from worktree_manager import mux_companion as companion
from worktree_manager.companion_bridge_tool_statistics import (
    ToolStatisticsProjection, decode_tool_statistics,
)
from worktree_manager.companion_timeline_contract import TimelineContractError
from worktree_manager.companion_timeline_contract import LiveDelta
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from worktree_manager.companion_worktree_source import WorktreeTimelineSource
from test_companion_agenda_live import current_data, until
from test_companion_bridge_codec import OWNER
from test_companion_bridge_session_source import semantic_peer as semantic_peer, source
from test_companion_bridge_session_codec import semantic as semantic

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX private CLI pipe; native qualification is separate"),
]


def statistics_body() -> dict:
    return {
        "version": 1, "availability": "observed", "reason": "native-event-range",
        "basis": "native-tool-start-record-occurrences", "coverage": "partial",
        "durability": "unknown", "start_occurrences": 7, "observed_records": 10,
        "floor": 1, "head": 10, "contiguous": True,
    }


def decode(body: dict):
    return decode_tool_statistics(body, {"journal_incarnation": "fixture"}, {"floor": 1, "head": 10})


@pytest.mark.parametrize("mutate", [
    lambda body: body.update(version=True),
    lambda body: body.update(start_occurrences=True),
    lambda body: body.update(start_occurrences=7.0),
    lambda body: body.update(start_occurrences=11),
    lambda body: body.update(observed_records=0),
    lambda body: body.update(observed_records=1 << 53),
    lambda body: body.update(floor=0),
    lambda body: body.update(head=11),
    lambda body: body.update(contiguous=1),
    lambda body: body.update(contiguous=False),
    lambda body: body.update(coverage="complete"),
    lambda body: body.update(durability="durable"),
    lambda body: body.update(basis="unique-tool-calls"),
    lambda body: body.update(availability="unknown", reason="counter-unavailable"),
    lambda body: body.update(extra="unadmitted"),
])
def test_statistics_reject_imprecision_strengthened_coverage_and_cross_cut_values(mutate) -> None:
    body = statistics_body()
    mutate(body)
    with pytest.raises(TimelineContractError):
        decode(body)


def test_statistics_keep_last_qualified_cut_across_unknown_and_reconnect() -> None:
    original = decode(statistics_body())
    projection = ToolStatisticsProjection()
    assert projection.accept(original) and not projection.accept(original)
    unknown = statistics_body()
    unknown.update(
        availability="unknown", reason="counter-unavailable",
        **dict.fromkeys(("start_occurrences", "observed_records", "floor", "head", "contiguous")),
    )
    assert projection.accept(decode(unknown))
    for changed in (
        replace(original, head=9), replace(original, start_occurrences=6),
        replace(original, head=11, observed_records=11, start_occurrences=9),
    ):
        with pytest.raises(TimelineContractError, match="regressed"):
            projection.accept(changed)
    projection.invalidate()
    assert projection.current is None and projection.accept(original)
    # A different retained interval or source is explicitly scoped, never lifetime-looking.
    assert projection.accept(replace(original, floor=8, start_occurrences=1, observed_records=3))
    assert projection.accept(replace(original, source="replacement", head=2, observed_records=2,
                                     start_occurrences=0))
    assert "observed range" in original.compact()
    assert "Not unique calls, running work or lifetime totals" in original.summary()
    assert "unknown" in decode(unknown).compact()


def test_statistics_are_opt_in_atomic_and_never_accepted_from_history(semantic) -> None:
    semantic.log.append("turn_complete", {})
    codec = semantic.codec()
    body = semantic.frame(tool_statistics=True)
    with pytest.raises(TimelineContractError):
        codec.decode(body)
    first = codec.decode(body, tool_statistics=True)
    state = codec.statistics_state
    assert state.start_occurrences == 0 and state.observed_records == 1
    assert codec.decode(body, tool_statistics=True) == first and codec.statistics_state == state
    forged = dict(body, tool_statistics={**body["tool_statistics"], "head": 2})
    with pytest.raises(TimelineContractError, match="atomic source range"):
        codec.decode(forged, tool_statistics=True)
    assert codec.statistics_state == state
    history = semantic.frame(history=True)
    codec.decode(history, history=True)
    assert codec.statistics_state == state
    with pytest.raises(TimelineContractError):
        codec.decode({**history, "tool_statistics": body["tool_statistics"]}, history=True,
                     tool_statistics=True)
    semantic.log.rebuild([("tool_call_start", {"tool_call_id": "replacement"})])
    with pytest.raises(bridge.OwnerReadError, match="incarnation changed"):
        codec.decode(semantic.frame(tool_statistics=True), tool_statistics=True)
    replacement = semantic.codec()
    replacement.decode(semantic.frame(tool_statistics=True), tool_statistics=True)
    assert replacement.statistics_state.source != state.source
    assert replacement.statistics_state.start_occurrences == 1


@pytest.mark.timeout(90)
def test_actual_native_burst_exceeds_default_widget_window_without_resetting_owner_statistics(semantic) -> None:
    from test_companion_visual import VisualApp

    semantic.log.append("tool_call_start", {"tool_call_id": "same"})
    codec = semantic.codec()
    first = codec.decode(semantic.frame(tool_statistics=True), tool_statistics=True)
    token = codec.token(first, 1)
    projection = ToolStatisticsProjection()
    assert projection.accept(codec.statistics_state)
    window = TimelineWindow()
    assert window.bind(token).applied and window.apply_snapshot(codec.snapshot(first, token)).applied
    for _index in range(5_201):
        semantic.log.append("tool_call_start", {"tool_call_id": "same"})

    async def check() -> None:
        app = VisualApp(window, session=True)
        async with app.run_test(size=(100, 32)) as pilot:
            panel = app.query_one(CompanionTimelinePanel)
            app.query_one("#timeline-current-compact", Static).update(projection.current.compact())
            await pilot.pause()
            cursor, published, frames = first["position"], window.events[-1].position, 0
            while True:
                body = codec.decode(semantic.frame(position=cursor, tool_statistics=True),
                                    tool_statistics=True)
                state = codec.statistics_state
                projection.accept(state)
                assert state.start_occurrences == 5202 and state.head == 5202
                current = codec.position(token, body["position"])
                assert panel.apply_delta(LiveDelta(
                    token, published, current, codec.events(body, token),
                )).applied
                published, cursor, frames = current, body["position"], frames + 1
                app.query_one("#timeline-current-compact", Static).update(state.compact())
                app.query_one("#timeline-tool-statistics", Static).update(state.summary())
                if not body["has_more"]:
                    break
                assert frames < 300
                await pilot.pause()
            await pilot.pause()
            timeline = app.query_one(SubwayTimeline)
            assert window.max_events == 5000 and len(window.events) < 5202 and window.evicted > 0
            assert window.events[-1].event_id == "event:5202" and timeline.is_vertical_scroll_end
            assert projection.current.start_occurrences == 5202
            assert "5202 starts (observed range)" in str(
                app.query_one("#timeline-current-compact", Static).content,
            )
            assert frames > 1

    asyncio.run(check())


@pytest.mark.parametrize("older", ["old_statistics_cli", "old_statistics_provider", "both"])
def test_statistics_rollback_keeps_independent_current_request_support(
    semantic_peer, tmp_path, older: str,
) -> None:
    semantic_peer["old_statistics_provider" if older == "both" else older].touch()
    if older == "both":
        semantic_peer["old_provider"].touch()
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        reader, delivered = source(tmp_path), []
        reader._statistics = delivered.append
        initial = await reader.prepare(1, stop=asyncio.Event())
        assert initial.events and reader.commands.tool_statistics is False
        assert reader.commands.current_requests is (older != "both")
        assert not any(state is not None for state in delivered)
        assert all("tool_statistics" not in request["path"] for request in semantic_peer["requests"])
        calls = [json.loads(line) for line in semantic_peer["invocations"].read_text().splitlines()]
        assert sum("version" in call for call in calls) == 1
        if older == "both":
            # Two independently unsupported flags, bounded retries before the sole query.
            assert len(calls) == 4 and len(semantic_peer["requests"]) == 1

    asyncio.run(check())


def test_statistics_parser_failure_never_replays_baseline(semantic_peer, tmp_path) -> None:
    semantic_peer["reject_statistics"].touch()
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        with pytest.raises(bridge.OwnerReadError, match="unrecognized arguments"):
            await source(tmp_path).prepare(1, stop=asyncio.Event())
        assert not semantic_peer["requests"]
        calls = [json.loads(line) for line in semantic_peer["invocations"].read_text().splitlines()]
        assert len(calls) == 2

    asyncio.run(check())


@pytest.mark.parametrize("declaration", [True, {"version": True, "capabilities": []}, {
    "version": 1, "capabilities": "observe-session-tool-statistics",
}])
def test_malformed_statistics_declaration_does_not_disable_current_state(
    semantic_peer, tmp_path, declaration,
) -> None:
    semantic_peer["declaration_override"].write_text(json.dumps({
        "schema": "agent-bridge.cli-contracts", "version": 1, "contracts": {
            "bridge-session-current-requests-v1": {
                "version": 1, "capabilities": ["observe-session-current-requests"],
            },
            "bridge-session-tool-statistics-v1": declaration,
        },
    }))
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        reader = source(tmp_path)
        assert (await reader.prepare(1, stop=asyncio.Event())).events
        assert reader.commands.current_requests is True and reader.commands.tool_statistics is False
        assert "current_requests=true" in semantic_peer["requests"][0]["path"]
        assert "tool_statistics" not in semantic_peer["requests"][0]["path"]

    asyncio.run(check())


@pytest.mark.parametrize("size", [(110, 40), (60, 24)])
def test_actual_large_owner_range_reaches_sparse_widget_independently_of_history_and_eviction(
    semantic_peer, tmp_path, monkeypatch, record_property, size,
) -> None:
    from test_companion_visual import offline_svg

    def data():
        result = current_data()
        result.worktree["path"] = str(tmp_path)
        result.bundle = {"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}}
        return result

    async def unavailable(*args, **kwargs):
        raise bridge.OwnerReadError("unavailable", "fixture Worktrees source unavailable")

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(WorktreeTimelineSource, "prepare", unavailable)
    for _index in range(5_201):
        # Distinct native occurrences with the same reported tool ID are not unique calls.
        semantic_peer["log"].append("tool_call_start", {"tool_call_id": "repeated"})

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        # Lower only this fixture's display budget to exercise eviction with two pages.
        app._timeline_panel.window.max_events = 64
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "owned:child" in app._bridge_attachment._members)
            control = app.query_one("#bridge-feed", Select)
            control.focus()
            control.value = "owned:child"
            label = app.query_one("#timeline-current-compact", Static)
            await until(pilot, lambda: "5201 starts (observed range)" in str(label.content))
            await until(pilot, lambda: app._bridge_attachment.controller is not None
                        and app._bridge_attachment.tool_statistics is not None
                        and app._bridge_attachment.tool_statistics.start_occurrences == 5201)
            reader = app._bridge_attachment.controller._sources[next(iter(
                app._bridge_attachment.controller._sources
            ))]
            state = app._bridge_attachment.tool_statistics
            assert state.start_occurrences == 5201 and state.head == 5201
            assert len(app._timeline_panel.window.events) < 64
            assert app._bridge_attachment.controller.reader_count == 1
            focused = app.focused
            for _ in range(2):
                window = app._timeline_panel.window
                owner = window.sources[reader.initial.token.identity.source_key]
                assert owner.before is not None
                assert window.request_history(owner.token, owner.before).applied
                page = await reader.history(owner.token, owner.before, stop=asyncio.Event())
                assert app._timeline_panel.prepend_page(page).applied
                assert app._bridge_attachment.tool_statistics == state
            assert app._timeline_panel.window.evicted > 0
            semantic_peer["log"].append("tool_call_start", {"tool_call_id": "repeated"})
            await until(pilot, lambda: "5202 starts (observed range)" in str(label.content))
            assert app.focused is focused
            detail = str(app.query_one("#timeline-tool-statistics", Static).content)
            assert "range 1..5202" in detail and "partial coverage" in detail
            assert "lifetime totals" in detail and len(detail) < 512
            assert "repeated" not in detail
            record_property("sidecar-state", f"tool-statistics-{size[0]}")
            record_property("sidecar-svg", offline_svg(app.export_screenshot()))
            calls = [json.loads(line) for line in semantic_peer["invocations"].read_text().splitlines()]
            assert sum("version" in call for call in calls) == 1
            histories = [call for call in calls if "session-observation-history" in call]
            assert len(histories) == 2
            assert all("--tool-statistics" not in call and "--current-requests" not in call
                       for call in histories)
            assert semantic_peer["counts"]["max_held"] == 1
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await until(pilot, lambda: app._bridge_attachment.controller is None)
            assert app._bridge_attachment.tool_statistics is None
            assert "Tool starts unknown" in str(label.content)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "5202 starts (observed range)" in str(label.content))
            assert app._bridge_attachment.tool_statistics.start_occurrences == 5202

    asyncio.run(check())
