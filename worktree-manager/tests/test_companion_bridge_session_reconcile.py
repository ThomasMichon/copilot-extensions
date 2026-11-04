"""Refresh reacquires hosting generation without sacrificing exhausted-source evidence."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Select, Static, TabbedContent

from worktree_manager import companion_bridge_source as bridge
from worktree_manager import mux_companion as companion
from worktree_manager.companion_worktree_source import WorktreeTimelineSource
from test_companion_agenda_live import current_data, until
from test_companion_bridge_codec import OWNER
from test_companion_bridge_session_source import semantic_peer as semantic_peer, pytestmark as pytestmark


@pytest.mark.parametrize("mode", ["hosting-replaced", "hosting-replaced-hidden-refresh", "proof-exhausted"])
def test_refresh_reacquires_membership_then_selected_session(
    semantic_peer, tmp_path, monkeypatch, mode: str,
) -> None:
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
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "owned:child" in app._bridge_attachment._members)
            app.query_one("#bridge-feed", Select).value = "owned:child"
            await until(pilot, lambda: any(row.event_id == "event:1" for row in app._timeline_panel.window.events))
            await until(pilot, semantic_peer["following"].is_set)
            initial = next(row for row in app._timeline_panel.window.events if row.event_id == "event:1")
            key = initial.source.source_key
            old_window = app._timeline_panel.window
            initial_epoch = old_window.sources[key].token.request_epoch
            old_proofs = dict(old_window._proofs)
            hidden = mode == "hosting-replaced-hidden-refresh"
            if hidden:
                app.query_one("#views", TabbedContent).active = "summary-tab"
                await until(pilot, lambda: app._bridge_attachment.controller is None)
            if mode.startswith("hosting-replaced"):
                semantic_peer["manager"]._generation_id = "replacement-generation"
            else:
                old_window.max_proofs = 1
                semantic_peer["log"].append("permission_request", {"request_id": "input-next"})
                await until(pilot, lambda: key in old_window.reconciliation_required)
                with semantic_peer["log"]._lock:
                    semantic_peer["log"]._events = semantic_peer["log"]._events[-1:]
            request_count = len(semantic_peer["requests"])
            app.action_refresh()
            if hidden:
                await until(pilot, lambda: not app._loading and app._bridge_attachment.choice == "membership")
                assert app._bridge_attachment._chosen is None
                assert len(semantic_peer["requests"]) == request_count
                app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: not app._loading and app._bridge_attachment.choice == "membership"
                        and "child: starting" in str(app.query_one("#timeline-bridge-status", Static).content))
            assert app._bridge_attachment._chosen is None
            app.query_one("#bridge-feed", Select).value = "owned:child"
            expected_event = "event:1" if mode.startswith("hosting-replaced") else "event:2"

            def ready() -> bool:
                state = app._timeline_panel.window.sources.get(key)
                return (
                    state is not None and state.availability.value == "observed"
                    and state.token.request_epoch > initial_epoch and any(
                        row.event_id == expected_event and row.source == state.token.identity
                        for row in app._timeline_panel.window.events
                    )
                )

            await until(pilot, ready)
            state = app._timeline_panel.window.sources[key]
            assert state.availability.value == "observed"
            if mode.startswith("hosting-replaced"):
                assert state.token.identity.generation != initial.source.generation
                assert app._bridge_attachment._chosen[1] == "replacement-generation"
            else:
                assert state.token.identity == initial.source
                assert key not in app._timeline_panel.window.reconciliation_required
                retained = app._timeline_attachment.retained_windows
                assert retained == (old_window,)
                assert all(old_window._proofs[ref] == value for ref, value in old_proofs.items())
            assert app._bridge_attachment.controller.reader_count == 1

    asyncio.run(check())
