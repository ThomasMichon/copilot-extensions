"""Conversation recency and native, selection-safe list controls."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from textual.widgets import OptionList

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.capture import _wait_for_initial_setup
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen


@pytest.mark.parametrize("activity,expected", [
    ({"version": 1, "last_message_at": "2026-11-06T17:59:00-07:00"}, "1m"),
    ({"version": 1, "last_message_at": "2026-11-07T01:00:00Z"}, "0m"),
    ({"version": 2, "last_message_at": "2026-11-07T00:59:00Z"}, "2h"),
    ({"version": 1, "last_message_at": "invalid"}, "2h"),
    (None, "2h"),
])
def test_used_display_and_sort_share_message_instant_with_legacy_fallback(monkeypatch, activity, expected):
    now = datetime.fromisoformat("2026-11-07T01:00:00+00:00").astimezone().replace(tzinfo=None)
    monkeypatch.setattr(derive, "NOW", now)
    raw = {
        "id": "fixture-one", "status": "active", "state": "wip",
        "started_at": "2026-11-01T00:00:00Z",
        "last_resumed_at": "2026-11-06T23:00:00Z", "message_activity": activity,
    }
    row = derive.norm(raw, "host", "Win")
    assert row["used"] == expected
    expected_seconds = {"1m": 60, "0m": 0, "2h": 7200}[expected]
    assert derive._last_active_secs(row) == expected_seconds
    assert derive.time_fields(raw)["used_at"] == row["used_at"]


def test_invalid_legacy_resume_falls_back_to_the_same_valid_instant_for_display_and_sort(monkeypatch):
    monkeypatch.setattr(derive, "NOW", datetime(2026, 11, 7, 1))
    row = derive.norm({
        "id": "fixture-one", "status": "active", "state": "wip",
        "last_resumed_at": "invalid", "started_at": "2026-11-07T00:00:00",
    }, "host", "Win")
    assert row["used"] == "1h"
    assert derive._last_active_secs(row) == 3600


@pytest.mark.parametrize("width", [48, 118])
def test_native_controls_combine_completed_preserve_focus_and_disclose_hidden_live(monkeypatch, width):
    monkeypatch.setattr(derive, "NOW", datetime(2026, 11, 7, 1))
    local = ("fixture-host", "Win")
    raws = [
        {"id": "fixture-live", "title": "Live", "state": "active", "mux_session": True},
        {"id": "fixture-wip", "title": "Zeta unfinished", "state": "wip",
         "message_activity": {"version": 1, "last_message_at": "2026-11-07T00:50:00"}},
        {"id": "fixture-done", "title": "Alpha completed", "state": "completed",
         "message_activity": {"version": 1, "last_message_at": "2026-11-07T00:59:00"}},
    ]
    for raw in raws:
        raw.update(status="active", started_at="2026-11-01T00:00:00", session_count=1)
    source = SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="Fixture", bucket=derive.bucket, for_machine=derive.for_machine,
        is_local=lambda m, e: (m, e) == local,
        machines=lambda: [("Fixture", *local, True)],
        load=lambda: [derive.norm(raw, *local) for raw in raws],
    )

    async def run():
        app = PickerApp(source, live=False)
        async with app.run_test(size=(width, 32)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(scr, pilot)
            await pilot.pause()
            scr.machine_idx = scr.local_index()
            scr.sel = ("L", 1)
            scr.wt_anchor = 1
            scr._sync_focus_to_sel()
            await pilot.pause()
            sections = [(name, rows) for name, rows in scr.current_list_visible()[1] if rows]
            assert [name for name, _ in sections] == ["Active", "Recent"]
            assert [row["raw"]["id"] for row in sections[1][1]] == ["fixture-done", "fixture-wip"]
            selected = scr._wt_visible_records()[1]["selection_id"]
            assert scr._selected_record()["selection_id"] == selected
            native = scr.query_one("#nf-body-data")
            native._sync_from_sel()
            stale_highlight = OptionList.OptionHighlighted(
                native, native.get_option_at_index(native.highlighted), native.highlighted,
            )
            raws[1]["message_activity"]["last_message_at"] = "2026-11-07T01:00:00"
            scr._wt_remap(lambda: setattr(scr, "data", source.load()))
            scr.refresh()
            await pilot.pause()
            native = scr.query_one("#nf-body-data")
            visible_keys = [row["selection_id"] for row in scr._wt_visible_records()]
            assert list(native._l_rows) == visible_keys
            assert scr._selected_record()["selection_id"] == selected
            assert scr.sel == ("L", 2)
            native.post_message(stale_highlight)
            await pilot.pause()
            assert scr._selected_record()["selection_id"] == selected
            await pilot.press("space", "s", "g")
            await pilot.pause()
            assert scr._selected_record()["selection_id"] == selected
            assert selected in scr.wt_sel.ids
            assert scr._wt_visible_records()[scr.wt_anchor]["selection_id"] == selected
            assert scr.list_view.group_index == 1
            assert "1 active" in scr.status_text().plain
            assert "1 unfinished" in scr.status_text().plain
            assert "1 completed" in scr.status_text().plain
            await pilot.press("f", "f", "f")
            await pilot.pause()
            assert [row["raw"]["id"] for row in scr._wt_visible_records()] == ["fixture-done"]
            assert len(scr.list_records()) == 3
            assert "completed (live hidden)" in scr._cmd_bar_row(width).plain
            assert selected in scr.wt_sel.ids
            await pilot.press("escape")
            await pilot.pause()
            assert scr.list_view.status_index == 0
            assert len(scr._wt_visible_records()) == 3

    asyncio.run(run())
