"""Conversation recency and native, selection-safe list controls."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def test_producer_normalizes_naive_message_before_another_timezone_consumes_it(tmp_path, monkeypatch):
    from agent_worktrees import sessions_messages as messages

    class ProducerDateTime(datetime):
        def astimezone(self, tz=None):
            if self.tzinfo is None:
                return self.replace(tzinfo=timezone(timedelta(hours=-7))).astimezone(tz)
            return super().astimezone(tz)

    monkeypatch.setattr(messages, "datetime", ProducerDateTime)
    (tmp_path / "events.jsonl").write_text(json.dumps({
        "type": "user.message", "timestamp": "2026-11-06T18:00:00",
    }) + "\n", encoding="utf-8")
    _turns, published = messages.read(tmp_path)
    assert published == "2026-11-07T01:00:00+00:00"
    assert json.loads((tmp_path / ".aw-turns.json").read_text())["last_message_at"] == published
    monkeypatch.setattr(derive, "NOW", datetime(2026, 11, 7, 1, 1, tzinfo=timezone.utc))
    raw = {"message_activity": {"version": 1, "last_message_at": published}}
    assert derive._last_active_secs(raw) == 60
    assert derive._last_active_display(raw) == "1m"


def test_used_and_sort_compare_utc_instants_across_repeated_dst_hour(monkeypatch):
    monkeypatch.setattr(derive, "NOW", datetime.fromisoformat("2026-11-01T02:10:00-05:00"))
    rows = [
        {"id": name, "message_activity": {"version": 1, "last_message_at": timestamp}}
        for name, timestamp in (
            ("old", "2026-11-01T01:50:00-04:00"),
            ("newer", "2026-11-01T01:10:00-05:00"),
            ("newest", "2026-11-01T01:50:00-05:00"),
        )
    ]
    assert [derive._last_active_secs(row) for row in rows] == [4800, 3600, 1200]
    assert [derive._last_active_display(row) for row in rows] == ["1h", "1h", "20m"]
    assert [row["id"] for row in sorted(rows, key=derive._last_active_secs)] == ["newest", "newer", "old"]


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


def test_same_id_cross_source_reorder_keeps_native_display_and_action_target_together(monkeypatch):
    monkeypatch.setattr(derive, "NOW", datetime(2026, 11, 7, 1))
    local = ("fixture-a", "Win")
    machines = [local, ("fixture-b", "Win")]
    raws = [
        {"id": "same-worktree", "title": title, "state": "wip", "status": "active",
         "started_at": "2026-11-01T00:00:00", "session_count": 1,
         "message_activity": {"version": 1, "last_message_at": timestamp}}
        for title, timestamp in (
            ("Source A target", "2026-11-07T00:59:00"),
            ("Source B target", "2026-11-07T00:50:00"),
        )
    ]
    source = SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="Fixture", bucket=derive.bucket, for_machine=derive.for_machine,
        is_local=lambda m, e: (m, e) == local,
        machines=lambda: [(m, m, e, True) for m, e in machines],
        load=lambda: [derive.norm(raw, *machine) for raw, machine in zip(raws, machines)],
    )

    async def run():
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 32)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(scr, pilot)
            scr.machine_idx = 0
            scr.load_delay = {index: 0 for index in scr.load_delay}
            scr.sel, scr.wt_anchor = ("L", 0), 0
            scr.refresh()
            scr._sync_focus_to_sel()
            await pilot.pause()
            before = [row["selection_id"] for row in scr._wt_visible_records()]
            assert len(before) == 2 and before[0] != before[1]
            scr.wt_sel.replace({before[0]})
            raws[1]["message_activity"]["last_message_at"] = "2026-11-07T01:00:00"
            scr._wt_remap(lambda: setattr(scr, "data", source.load()))
            scr.refresh()
            await pilot.pause()
            native = scr.query_one("#nf-body-data")
            assert [row["selection_id"] for row in scr._wt_visible_records()] == before[::-1]
            assert list(native._l_rows) == before[::-1]
            assert scr._selected_record()["selection_id"] == before[0]
            assert scr._wt_visible_records()[scr.wt_anchor]["selection_id"] == before[0]
            assert scr.wt_sel.ids == {before[0]}
            assert native.highlighted == native._stops.index(scr.sel)
            assert "Source A target" in native.get_option_at_index(native.highlighted + 1).prompt.plain

    asyncio.run(run())


def test_live_status_filter_preserves_finalized_attached_mux(monkeypatch):
    from agent_worktrees import __main__ as main, git_ops, tracking

    monkeypatch.setattr(derive, "NOW", datetime(2026, 11, 7, 1))
    monkeypatch.setattr("agent_worktrees.finalized_checkout.matches", lambda record: True)
    local = ("fixture-host", "Win")
    record = tracking.WorktreeRecord(
        worktree_id="fixture-final", branch="worktree/fixture-final",
        worktree_path=str(Path("fixture-checkout").resolve()), repo="fixture",
        machine=local[0], platform="windows", started_at="2026-11-01T00:00:00",
        last_resumed_at=None, resume_count=0, title="Fixture",
        status="finalized", completed_at=None,
    )
    raw = main._worktree_to_dict(record, state_info=git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED,
    ))
    rows = [
        derive.norm({
            **raw, "id": name, "title": name, "session_count": 1,
            "mux_session": live, "mux_attached": live,
        }, *local)
        for name, live in (("final-live", True), ("final-stopped", False))
    ]
    assert rows[0]["state"] == "FINAL" and rows[0]["mux_live"]
    source = SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="Fixture", bucket=derive.bucket, for_machine=derive.for_machine,
        is_local=lambda m, e: (m, e) == local,
        machines=lambda: [("Fixture", *local, True)], load=lambda: rows,
    )

    async def run():
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 32)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(scr, pilot)
            scr.list_view.status_index = 1
            scr.refresh()
            await pilot.pause()
            assert [row["id"] for row in scr._wt_visible_records()] == ["final-live"]
            scr.list_view.status_index = 3
            assert {row["id"] for row in scr._wt_visible_records()} == {"final-live", "final-stopped"}

    asyncio.run(run())
