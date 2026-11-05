"""Three actual private owners remain independent during browsing and retirement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import importlib
import time
from pathlib import Path
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import Collapsible, Select, Static, TabbedContent

from worktree_manager import companion_bridge_source as bridge
from worktree_manager import mux_companion as companion
from worktree_manager.agenda_transport import STARTUP_TIMEOUT
from worktree_manager.companion_timeline_contract import Availability, Provider
from worktree_manager.companion_timeline_widget import SubwayTimeline
from _companion_joined_fixtures import managed_join
from _worktree_timeline_fixtures import JOIN_COMMAND_CLEANUP, JOIN_PREPARATION_SECONDS, JoinPhase
from test_companion_bridge_session_source import semantic_peer as semantic_peer, pytestmark as pytestmark
from test_companion_current_requests import actual_waiters as actual_waiters
from test_companion_visual import offline_svg


async def publish_agenda(peer: dict[str, Any]) -> None:
    result = await asyncio.to_thread(
        peer["agenda_publisher"].publish, peer["agenda_home"], [],
        provider_python=peer["provider_python"], provider_project="example",
    )
    assert result["state"] == "delivered", result


async def ready(
    app: companion.MuxCompanionApp, pilot: Pilot, peer: dict[str, Any],
    condition: Callable[[], bool], phase: str, *, seconds: float | None = None,
) -> None:
    await JoinPhase(
        phase, seconds if seconds is not None else
        JOIN_PREPARATION_SECONDS + bridge.BRIDGE_STARTUP_TIMEOUT + JOIN_COMMAND_CLEANUP
        + 4 * (STARTUP_TIMEOUT + JOIN_COMMAND_CLEANUP),
    ).wait(
        condition, pilot.pause,
        lambda: next(iter([*peer["failures"], *peer.get("bridge", {}).get("failures", [])]), None),
        lambda: {
            "owner": str(app.query_one(".timeline-state", Static).content),
            "bridge": str(app.query_one("#timeline-bridge-status", Static).content),
            "stages": peer["stages"],
            "bridge_requests": peer.get("bridge", {}).get("requests", []),
        },
    )


@pytest.mark.timeout(180)
def test_joined_sparse_healthy_demo(
    semantic_peer: dict[str, Any], actual_waiters: Callable[[str], Awaitable[dict[str, Any]]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    semantic_peer["log"].append("subagent_started", {"agent_id": "comparison"})
    semantic_peer["log"].append("turn_complete", {})
    with managed_join(tmp_path / "healthy-owners", monkeypatch, clock_origin=time.time() - 60) as peer:
        peer["bridge"] = semantic_peer

        async def check() -> None:
            await actual_waiters("clear")
            app = companion.MuxCompanionApp(cwd=str(peer["checkout"]))
            async with app.run_test(size=(110, 40)) as pilot:
                await ready(app, pilot, peer, lambda: app._data.agenda is not None, "healthy-coarse-agenda")
                await publish_agenda(peer)
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                timeline = app.query_one(SubwayTimeline)
                await ready(
                    app, pilot, peer,
                    lambda: {state.token.identity.provider for state in timeline.window.sources.values()} == set(Provider)
                    and all(state.availability is Availability.OBSERVED for state in timeline.window.sources.values())
                    and "owned:child" in app._bridge_attachment._members
                    and app._rich_agenda_attachment.token is not None,
                    "healthy-three-owner-admission",
                )
                assert await pilot.click("CollapsibleTitle")
                app.query_one("#bridge-feed", Select).focus()
                await pilot.press("enter", "down", "enter")
                await ready(
                    app, pilot, peer,
                    lambda: app._bridge_attachment.current_requests is not None
                    and app._bridge_attachment.current_requests.availability is Availability.OBSERVED
                    and any(row.event_id == "event:2" for row in timeline.window.events),
                    "healthy-selected-session",
                )
                assert app._bridge_attachment.choice == "owned:child"
                assert await pilot.click("CollapsibleTitle")
                timeline.focus()
                for name, size in (("healthy-full", (110, 40)), ("healthy-narrow", (60, 24))):
                    await pilot.resize_terminal(*size)
                    await pilot.pause()
                    assert app.query_one(Collapsible).collapsed
                    assert timeline.size.height >= size[1] // 2
                    assert app._timeline_panel.window.last_result.applied
                    assert all(state.availability in {Availability.OBSERVED, Availability.UNKNOWN}
                               for state in timeline.window.sources.values())
                    assert not app._timeline_panel._provider_notices
                    assert "source-error" not in str(app.query_one(".timeline-health", Static).content)
                    record_property("sidecar-state", name)
                    record_property("sidecar-svg", offline_svg(app.export_screenshot()))
                app.query_one("#views", TabbedContent).active = "summary-tab"
                await ready(
                    app, pilot, peer,
                    lambda: app._bridge_attachment.controller is None
                    and semantic_peer["session"].subscriber_count == 0,
                    "healthy-owner-retirement",
                )
        asyncio.run(check())
    assert not peer["failures"] and not semantic_peer["failures"]


@pytest.mark.timeout(180)
@pytest.mark.parametrize("retirement", ["hide", "head-loss", "history-gap"])
def test_joined_browsing_slow_page_live_waiter_and_cancellation(
    semantic_peer: dict[str, Any], actual_waiters: Callable[[str], Awaitable[dict[str, Any]]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None], retirement: str,
) -> None:
    for number in range(140):
        semantic_peer["log"].append("tool_call_start", {"tool_call_id": str(number)})

    with managed_join(tmp_path / "joined-owner", monkeypatch) as peer:
        peer["bridge"] = semantic_peer
        async def check() -> None:
            await actual_waiters("input")
            app = companion.MuxCompanionApp(cwd=str(peer["checkout"]))
            async with app.run_test(size=(110, 40)) as pilot:
                await ready(app, pilot, peer, lambda: app._data.agenda is not None, "coarse-agenda")
                await publish_agenda(peer)
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                timeline = app.query_one(SubwayTimeline)
                await ready(
                    app, pilot, peer,
                    lambda: {state.token.identity.provider for state in timeline.window.sources.values()} == set(Provider)
                    and "owned:child" in app._bridge_attachment._members
                    and app._rich_agenda_attachment.token is not None,
                    "three-owner-admission",
                )
                app.query_one("#bridge-feed", Select).value = "owned:child"
                await ready(
                    app, pilot, peer,
                    lambda: app._bridge_attachment.controller is not None
                    and app._bridge_attachment.current_requests is not None
                    and any(row.event_id == "event:140" for row in timeline.window.events),
                    "selected-session-admission",
                )
                controller = app._bridge_attachment.controller
                source = controller._sources[Provider.BRIDGE]
                state = next(state for state in timeline.window.sources.values()
                             if state.token.identity.source_key.startswith("bridge-session:"))
                assert state.before is not None
                timeline.focus()
                for _ in range(10):
                    if all(
                        value.before is None for value in timeline.window.sources.values()
                        if value.token.identity.provider is not Provider.BRIDGE
                    ):
                        break
                    await pilot.press("home")
                    await ready(
                        app, pilot, peer,
                        lambda: all(value.requested_before is None for value in timeline.window.sources.values()),
                        "independent-owner-history",
                    )
                else:
                    pytest.fail("bounded owner fixtures did not exhaust before selected-session paging")
                current = app._bridge_attachment.current_requests
                live_cursor, page_cursor = source._cursor, state.before
                retained = tuple(timeline.window.events)
                semantic_peer["block_history"].set()
                await pilot.press("home")
                await ready(
                    app, pilot, peer, semantic_peer["history_entered"].is_set, "blocked-history",
                )
                assert controller.page_count == 1 and not timeline.window.follow_live
                viewport = app._timeline_panel.capture_viewport()
                selected, focused = timeline.selected, app.focused
                peer["publish"]("Purpose changed while browsing", 4)
                store, home = peer["agenda_store"], peer["agenda_home"]
                agenda = store.read(home)
                cursor = agenda["documents"]["cursor"]
                frame_id = cursor["frames"][-1]
                store.mutate(home, {
                    "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
                    "operation_id": "joined-browsing-wait", "expected_revision": agenda["revision"],
                    "change": {
                        "operation": "wait", "frame_id": frame_id,
                        "item_id": cursor["frame_state"][frame_id]["pending"][0],
                        "reason_kind": "external", "reason_ref": "joined-browsing",
                        "next_action": "Wait for the isolated page",
                        "evidence": [{"criterion": "joined input", "kind": "measured",
                                      "ref": "joined-browsing", "inputs": "isolated fixture"}],
                    },
                }, session="head-a")
                await publish_agenda(peer)
                await actual_waiters("clear")
                await ready(
                    app, pilot, peer,
                    lambda: app._bridge_attachment.current_requests is not None
                    and not app._bridge_attachment.current_requests.requests
                    and "Purpose changed while browsing" in str(app.query_one(".timeline-context", Static).content)
                    and sum(row.source.provider is Provider.AGENDA for row in timeline.window.events) == 2,
                    "independent-current-state",
                )
                assert current.requests and source._cursor == live_cursor
                assert controller.page_count == 1
                assert timeline.window.sources[state.token.identity.source_key].before == page_cursor
                assert semantic_peer["counts"] == {"held": 1, "max_held": 1}
                semantic_peer["log"].append("turn_complete", {})
                await ready(
                    app, pilot, peer,
                    lambda: any(row.event_id == "event:141" for row in timeline.window.events),
                    "live-during-blocked-history",
                )
                assert controller.page_count == 1 and source._cursor != live_cursor
                assert all(row in timeline.window.events for row in retained)
                assert app._timeline_panel.capture_viewport() == viewport
                assert timeline.selected == selected and app.focused is focused
                assert timeline.window.unread and not timeline.window.follow_live
                live_cursor = source._cursor
                semantic_peer["block_history"].clear()
                await ready(
                    app, pilot, peer,
                    lambda: controller.page_count == 0
                    and timeline.window.sources[state.token.identity.source_key].before != page_cursor,
                    "history-prepend",
                )
                assert source._cursor == live_cursor
                assert all(row in timeline.window.events for row in retained)
                assert min(
                    int(row.event_id.removeprefix("event:")) for row in timeline.window.events
                    if row.source == state.token.identity
                ) < min(
                    int(row.event_id.removeprefix("event:")) for row in retained
                    if row.source == state.token.identity
                )
                assert app._timeline_panel.capture_viewport() == viewport
                assert timeline.selected == selected and app.focused is focused
                assert len({row.ref for row in timeline.window.events}) == len(timeline.window.events)
                record_property("sidecar-state", "joined-browsing-" + retirement)
                record_property("sidecar-svg", offline_svg(app.export_screenshot()))

                semantic_peer["history_entered"].clear()
                semantic_peer["block_history"].set()
                await pilot.press("home")
                await ready(
                    app, pilot, peer, semantic_peer["history_entered"].is_set, "second-blocked-history",
                )
                requests = len(semantic_peer["requests"])
                if retirement == "history-gap":
                    independent = tuple(row for row in timeline.window.events
                                        if row.source.provider is not Provider.BRIDGE)
                    requested = timeline.window.sources[state.token.identity.source_key].requested_before
                    assert requested is not None
                    boundary = int(requested.cursor.removeprefix("event:"))
                    with semantic_peer["log"]._lock:
                        semantic_peer["log"]._events = [
                            row for row in semantic_peer["log"]._events if row.id > boundary
                        ]
                        assert boundary < semantic_peer["log"]._events[0].id
                    semantic_peer["block_history"].clear()
                    await ready(
                        app, pilot, peer,
                        lambda: timeline.window.sources[state.token.identity.source_key].availability is Availability.UNAVAILABLE,
                        "retained-history-gap",
                        seconds=bridge.BRIDGE_STARTUP_TIMEOUT + JOIN_COMMAND_CLEANUP,
                    )
                    assert "retention-gap" in str(app.query_one("#timeline-bridge-status", Static).content)
                    assert all(row in timeline.window.events for row in retained)
                    assert all(row in timeline.window.events for row in independent)
                    assert app._bridge_attachment.current_requests is None
                    record_property("sidecar-state", "joined-history-gap")
                    record_property("sidecar-svg", offline_svg(app.export_screenshot()))
                    old_epoch = state.token.request_epoch
                    app.action_refresh()
                    await ready(
                        app, pilot, peer,
                        lambda: app._bridge_attachment.choice == "membership"
                        and "owned:child" in app._bridge_attachment._members
                        and app._bridge_attachment.controller is not None,
                        "gap-refresh-membership",
                    )
                    app.query_one("#bridge-feed", Select).value = "owned:child"
                    await ready(
                        app, pilot, peer,
                        lambda: app._bridge_attachment.current_requests is not None
                        and not app._bridge_attachment.current_requests.requests
                        and timeline.window.sources[state.token.identity.source_key].availability is Availability.OBSERVED
                        and timeline.window.sources[state.token.identity.source_key].token.request_epoch > old_epoch,
                        "gap-refreshed-session",
                    )
                    assert all(row in timeline.window.events for row in independent)
                    semantic_peer["log"].append("turn_complete", {})
                    await ready(
                        app, pilot, peer,
                        lambda: any(row.event_id == "event:142" for row in timeline.window.events),
                        "post-gap-live",
                    )
                    record_property("sidecar-state", "joined-gap-reconciled")
                    record_property("sidecar-svg", offline_svg(app.export_screenshot()))
                    app.query_one("#views", TabbedContent).active = "summary-tab"
                    requests = len(semantic_peer["requests"])
                elif retirement == "hide":
                    app.query_one("#views", TabbedContent).active = "summary-tab"
                else:
                    app._receive_agenda(
                        app._data, app._agenda_generation, app._agenda_cancel,
                        {**app._data.agenda, "availability": "head-changed", "observation": None}, None,
                    )
                await ready(
                    app, pilot, peer,
                    lambda: app._bridge_attachment.controller is None
                    and app._timeline_attachment.controller is None
                    and semantic_peer["session"].subscriber_count == 0
                    and semantic_peer["counts"]["held"] == 0,
                    "joined-retirement",
                )
                assert controller.page_count == controller.reader_count == 0
                assert app._bridge_attachment.current_requests is None
                semantic_peer["block_history"].clear()
                await actual_waiters("permission")
                semantic_peer["log"].append("ask_user_request", {"request_id": "late-request"})
                await pilot.pause()
                assert len(semantic_peer["requests"]) == requests
                assert app._bridge_attachment.current_requests is None
                if retirement == "head-loss":
                    assert not timeline.window.events
                    assert all(state.availability is Availability.HEAD_CHANGED
                               for state in timeline.window.sources.values())
                elif retirement == "hide":
                    assert all(row in timeline.window.events for row in retained)
            await JoinPhase("all-owner-cli-retirement", JOIN_COMMAND_CLEANUP).wait(
                peer["settled"], asyncio.sleep,
                lambda: peer["failures"][0] if peer["failures"] else None,
                lambda: {"trace": peer["trace"].read_text(), "stages": peer["stages"]},
            )
            locks = importlib.import_module("agent_worktrees.locks")
            assert all(
                not locks.pid_alive(row["pid"]) or locks.process_start_time(row["pid"]) != row["birth"]
                for row in semantic_peer["requests"]
            )

        asyncio.run(check())
