"""Large native roster formatting stays bounded by the painted viewport."""
from __future__ import annotations

import asyncio
import datetime
import os
import time
import types

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_views import WorktreesView


def test_native_viewport_formats_only_painted_rows(monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    monkeypatch.setattr(derive, "NOW", datetime.datetime(2026, 6, 27, 18))
    local = ("example", "Win")
    rows = [
        derive.norm({
            "id": f"example-win-20260627-{i:04d}", "title": f"Worktree {i}",
            "status": "active", "started_at": "2026-06-27T17:00:00",
            "turn_count": 3, "state": "wip", "picker_hidden": i >= 40,
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
            assert len(screen.list_records()) == 40
            timings = []
            for _ in range(3):
                formatted.clear()
                painted = asyncio.Event()
                started = time.perf_counter()
                screen.show_hidden = True
                screen.refresh()
                app.call_after_refresh(painted.set)
                await asyncio.wait_for(painted.wait(), 2)
                app.screen._compositor.render_full_update()
                timings.append(time.perf_counter() - started)
                assert len(screen.list_records()) == 1100
                assert body.option_count >= 2200
                assert len(body._lines) == body.option_count
                assert len(set(formatted)) <= body.size.height + 2
                screen.show_hidden = False
                screen.refresh()
                await pilot.pause()
            body.focus()
            await pilot.pause()
            body.highlighted = body._stops.index(("L", 39))
            await pilot.pause()
            assert screen.sel == ("L", 39)
            assert body.scroll_offset.y > 0
            print("viewport rebuild-to-painted ms:", [round(t * 1000, 2) for t in timings])
            if os.environ.get("WORKTREE_MANAGER_RENDER_BUDGET") == "1":
                assert max(timings) < 0.2

    asyncio.run(run())
