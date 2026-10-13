"""Large native roster formatting stays bounded by the painted viewport."""
from __future__ import annotations

import asyncio
import datetime
import gc
import os
import time
import types

import pytest

from worktree_manager.production_picker.picker_tui import derive, pivots
from worktree_manager.production_picker.picker_tui.deferred_row import DeferredRow
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_views import WorktreesView


@pytest.mark.parametrize("with_workers", [False, True])
def test_native_viewport_formats_only_painted_rows(monkeypatch, with_workers):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    monkeypatch.setattr(derive, "NOW", datetime.datetime(2026, 6, 27, 18))
    local = ("example", "Win")
    rows = [
        derive.norm({
            "id": f"example-win-20260627-{i:04d}", "title": f"Worktree {i}",
            "status": "active", "started_at": "2026-06-27T17:00:00",
            "turn_count": 3, "state": "wip", "picker_hidden": i >= 40,
            "mux_session": True, "mux_attached": True, "mux_clients": 1,
        }, *local)
        for i in range(1100)
    ]
    source = types.SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="example",
        machines=lambda: [("example Win", *local, True)],
        bucket=derive.bucket, for_machine=derive.for_machine, load=lambda: rows,
    )
    formatted = []
    original = WorktreesView._row_text

    def render(view, rec, *args, **kwargs):
        formatted.append(rec["id"])
        return original(view, rec, *args, **kwargs)

    monkeypatch.setattr(WorktreesView, "_row_text", render)
    gc_start = {}
    gc_spans = []

    def gc_event(phase, info):
        generation = info["generation"]
        if phase == "start":
            gc_start[generation] = time.perf_counter()
        else:
            gc_spans.append((gc_start.pop(generation), time.perf_counter(), generation))

    async def run():
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            screen = app.query_one(PickerScreen)
            for _ in range(50):
                if screen._setup_applied_epoch == screen._setup_epoch != 0:
                    break
                await pilot.pause()
            assert screen._setup_applied_epoch == screen._setup_epoch != 0
            body = screen.query_one("#nf-body-data")
            worker_reads = []
            if with_workers:
                worker_rows = [
                    {"name": f"worker-{i}", "worktree_id": row["id"],
                     "live": "LIVE", "activity": f"Worker activity {i}"}
                    for i, row in enumerate(rows)
                ]
                reg = pivots.parse_manifest(
                    {"label": "Workers", "list": ["example-workers", "list"],
                     "entry": {"id": "name"},
                     "worker": {"worktree": "worktree_id", "live": "live",
                                "activity": "activity"}},
                    name="workers", source_path="workers.json",
                )
                screen.pivots.append({"kind": "registered", "label": reg.label, "pivot": reg})
                screen.htabs.append(reg.label)

                def get_workers(scope):
                    worker_reads.append(scope)
                    return "ready", worker_rows, ""

                screen._pivot_runtimes[reg.name] = types.SimpleNamespace(get=get_workers)
            assert len(screen.list_records()) == 40
            baseline_painted = asyncio.Event()
            screen.refresh()
            app.call_after_refresh(baseline_painted.set)
            await asyncio.wait_for(baseline_painted.wait(), 2)
            app.screen._compositor.render_full_update()
            assert body.option_count >= 80
            timings = []
            cpu_timings = []
            gc_timings = []
            for _ in range(3):
                formatted.clear()
                worker_reads.clear()
                painted = asyncio.Event()
                started = time.perf_counter()
                cpu_started = time.process_time()
                screen.show_hidden = True
                screen.refresh()
                app.call_after_refresh(painted.set)
                await asyncio.wait_for(painted.wait(), 2)
                app.screen._compositor.render_full_update()
                timings.append(time.perf_counter() - started)
                cpu_timings.append(time.process_time() - cpu_started)
                gc_timings.append(sum(end - begin for begin, end, _gen in gc_spans
                                      if begin >= started))
                assert len(screen.list_records()) == 1100
                assert body.option_count >= 2200
                assert len(body._lines) == body.option_count
                assert len(set(formatted)) <= body.size.height + 2
                assert len(worker_reads) <= 12
                screen.show_hidden = False
                screen.refresh()
                await pilot.pause()
            screen.show_hidden = True
            screen.refresh()
            await pilot.pause()
            formatted.clear()
            painted = asyncio.Event()
            started = time.perf_counter()
            cpu_started = time.process_time()
            screen.pulse = 1 - screen.pulse
            screen.refresh(cause="pulse")
            app.call_after_refresh(painted.set)
            await asyncio.wait_for(painted.wait(), 2)
            app.screen._compositor.render_full_update()
            timings.append(time.perf_counter() - started)
            cpu_timings.append(time.process_time() - cpu_started)
            gc_timings.append(sum(end - begin for begin, end, _gen in gc_spans
                                  if begin >= started))
            assert len(set(formatted)) <= body.size.height + 2
            assert all(isinstance(body.get_option_at_index(index).prompt, DeferredRow)
                       for index, _rec, _li, _alt in body._l_rows.values())
            offscreen = screen.list_records()[-1]
            key = screen._row_key(offscreen)
            formatted.clear()
            screen.wt_sel.replace({key})
            body.refresh_data()
            assert not formatted
            position = next(i for i, row in enumerate(screen.data)
                            if screen._row_key(row) == key)
            screen.data[position] = dict(screen.data[position], activity="Updated activity")
            body.refresh_data()
            assert not formatted
            index = body._l_rows[key][0]
            assert "Updated activity" in body.get_option_at_index(index + 1).prompt.plain
            if with_workers:
                worker = next(row for row in worker_rows
                              if row["worktree_id"] == offscreen["id"])
                worker["activity"] = "Changed worker"
                body.refresh_data()
                index = body._l_rows[key][0]
                assert "Changed worker" in body.get_option_at_index(index + 2).prompt.plain
            body.focus()
            await pilot.pause()
            body.highlighted = body._stops.index(("L", 39))
            await pilot.pause()
            assert screen.sel == ("L", 39)
            assert body.scroll_offset.y > 0
            print("viewport rebuild/pulse-to-painted ms:",
                  [round(t * 1000, 2) for t in timings])
            print("process CPU ms:", [round(t * 1000, 2) for t in cpu_timings],
                  "GC ms:", [round(t * 1000, 2) for t in gc_timings])
            if os.environ.get("WORKTREE_MANAGER_RENDER_BUDGET") == "1":
                assert max(timings) < 0.2

    gc.callbacks.append(gc_event)
    try:
        asyncio.run(run())
    finally:
        gc.callbacks.remove(gc_event)
