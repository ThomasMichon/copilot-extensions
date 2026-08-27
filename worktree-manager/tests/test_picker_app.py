"""Tests for the demo (fake engine) + the Textual Picker (Phase 6b slice 2).

The Picker reaches data only across the process boundary, so these drive the
bundled fake engine (Aperture Labs) end-to-end -- subprocess spawn, JSON parse,
dataclass mapping, and a headless render/screenshot -- with no live engine.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
from contextlib import redirect_stdout
from dataclasses import replace

import pytest

from worktree_manager import demo, demo_engine
from worktree_manager import engine_client as ec
from worktree_manager import picker_app
from worktree_manager.engine_client import EngineError, Worktree
from worktree_manager.pivot_runtime import PivotLoadError, PivotPayload
from worktree_manager.plugin_contracts import parse_manifest


@pytest.fixture(autouse=True)
def _reset_engine_override():
    ec.set_engine_command(None)
    yield
    ec.set_engine_command(None)


def _run_demo_engine(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = demo_engine.main(args)
    return rc, buf.getvalue()


def test_demo_engine_emits_contract_json():
    rc, out = _run_demo_engine(["--project", "copilot-extensions", "list",
                                "--json", "--classify"])
    assert rc == 0
    obj = json.loads(out)
    assert obj["version"] == 1
    assert len(obj["worktrees"]) == len(demo.aperture_worktrees())
    assert obj["worktrees"][0]["repo"] == demo.DEMO_PROJECT


def test_demo_engine_unknown_verb_errors():
    rc, out = _run_demo_engine(["status"])
    assert rc == 2
    assert json.loads(out)["error"]


def test_demo_source_parses_across_the_boundary():
    src = picker_app.demo_source()
    worktrees = src()
    assert all(isinstance(w, Worktree) for w in worktrees)
    assert len(worktrees) == len(demo.aperture_worktrees())
    titles = " ".join(w.title or "" for w in worktrees)
    assert "lemons" in titles and "GLaDOS" in titles


def test_rows_to_text_renders_state_and_sync():
    src = picker_app.demo_source()
    text = picker_app.rows_to_text(src(), project=demo.DEMO_PROJECT)
    assert "Worktree Manager" in text
    assert "WIP DIRTY" in text
    assert "\u21913" in text  # ahead tag


def test_capture_svg_contains_title_and_demo_data():
    svg = picker_app.capture_svg(picker_app.demo_source(),
                                 project=demo.DEMO_PROJECT, size=(110, 32))
    assert "<svg" in svg
    assert "Worktree" in svg  # the app title (may be split across SVG spans)
    # A distinctive demo string proves the boundary fed the render.
    assert "lemons" in svg or "GLaDOS" in svg


def test_app_populates_table_from_source():
    fixture = [
        Worktree(id="aaaa1111", repo="r", machine="m", branch="b", title="hi",
                 state="wip", ahead=1, behind=0, dirty=False, status="active",
                 path="/x", raw={}),
    ]

    async def _run() -> int:
        app = picker_app.WorktreeManagerApp(lambda: list(fixture), project="r")
        async with app.run_test(size=(100, 24)):
            from textual.widgets import DataTable
            return app.query_one(DataTable).row_count

    assert asyncio.run(_run()) == 1


def test_app_opens_before_worktree_source_finishes():
    started = threading.Event()
    release = threading.Event()

    def slow_source():
        started.set()
        assert release.wait(2)
        return list(_FIX)

    async def _run() -> tuple[str, int]:
        app = picker_app.WorktreeManagerApp(slow_source, project="r")
        async with app.run_test(size=(100, 24)):
            assert await asyncio.to_thread(started.wait, 1)
            loading = app._last_status
            release.set()
            await app.workers.wait_for_complete()
            from textual.widgets import DataTable
            return loading, app.query_one(DataTable).row_count

    loading, row_count = asyncio.run(_run())
    assert "loading worktrees" in loading
    assert row_count == len(_FIX)


def test_app_engine_error_shows_status_not_crash():
    def boom():
        raise EngineError("nope", install_hint=True)

    async def _run() -> str:
        app = picker_app.WorktreeManagerApp(boom, project="r")
        async with app.run_test(size=(100, 24)):
            return app._last_status

    assert "engine unavailable" in asyncio.run(_run())


def _contribution(
    label: str,
    *,
    after: str = "Worktrees",
    columns: list[dict] | None = None,
):
    return parse_manifest(
        {
            "schema_version": 1,
            "label": label,
            "after": after,
            "list": ["agent-example", "list", "--machine", "{machine}"],
            "entry": {
                "id": "id",
                "title": "title",
                "subtitle": "detail",
                "badges": ["state"],
            },
            "columns": columns or [],
            "summary": "{ready} ready",
            "empty_hint": f"No {label.lower()}.",
        },
        name=label.lower(),
        marketplace="example",
        plugin=f"agent-{label.lower()}",
        source_path=f"/payload/{label.lower()}.json",
    )


def test_contributed_pivot_order_is_stable_and_resolves_forward_anchors():
    one = _contribution("One")
    two = _contribution("Two")
    child = _contribution("Child", after="Parent")
    parent = _contribution("Parent", after="Two")
    orphan = _contribution("Orphan", after="Missing")

    descriptors = picker_app.WorktreeManagerApp._build_pivots(
        [one, two, child, parent, orphan]
    )

    assert [descriptor.label for descriptor in descriptors] == [
        "Worktrees",
        "One",
        "Two",
        "Parent",
        "Child",
        "Orphan",
    ]


def test_contributed_pivot_loads_off_event_loop_and_renders_columns():
    started = threading.Event()
    release = threading.Event()
    contribution = _contribution(
        "Tasks",
        columns=[
            {"key": "title", "header": "task"},
            {"key": "state", "header": "state"},
        ],
    )

    def loader(pivot, context):
        assert threading.current_thread() is not threading.main_thread()
        assert context["machine"] == "m"
        started.set()
        assert release.wait(2)
        return PivotPayload(
            rows=({"id": "1", "title": "ship it", "state": "ready"},),
            summary={"ready": 1},
        )

    async def _run() -> tuple[str, str, int]:
        app = picker_app.WorktreeManagerApp(
            lambda: list(_FIX),
            project="r",
            contributions=[contribution],
            context_source=lambda: {"machine": "m"},
            pivot_loader=loader,
        )
        async with app.run_test(size=(100, 24)) as pilot:
            await app.workers.wait_for_complete()
            from textual.widgets import DataTable, Tabs
            app.query_one(Tabs).active = "pivot-1"
            await pilot.pause()
            assert await asyncio.to_thread(started.wait, 1)
            loading = app._last_status
            release.set()
            await app.workers.wait_for_complete()
            return loading, app._last_status, app.query_one(DataTable).row_count

    loading, ready, row_count = asyncio.run(_run())
    assert "loading tasks" in loading
    assert ready == "1 tasks · 1 ready · r: refresh · q: quit"
    assert row_count == 1


def test_contributed_pivot_failure_isolated_from_peer_and_worktrees():
    bad = _contribution("Bad")
    good = _contribution("Good")

    def loader(pivot, context):
        if pivot.label == "Bad":
            raise PivotLoadError("provider failed")
        return PivotPayload(rows=({"id": "1", "title": "healthy"},), summary={})

    async def _run() -> tuple[str, str, int]:
        app = picker_app.WorktreeManagerApp(
            lambda: list(_FIX),
            project="r",
            contributions=[bad, good],
            pivot_loader=loader,
        )
        async with app.run_test(size=(100, 24)) as pilot:
            await app.workers.wait_for_complete()
            from textual.widgets import DataTable, Tabs
            tabs = app.query_one(Tabs)
            tabs.active = "pivot-1"
            await pilot.pause()
            await app.workers.wait_for_complete()
            bad_status = app._last_status
            tabs.active = "pivot-2"
            await pilot.pause()
            await app.workers.wait_for_complete()
            good_status = app._last_status
            tabs.active = "pivot-0"
            await pilot.pause()
            worktree_rows = app.query_one(DataTable).row_count
            return bad_status, good_status, worktree_rows

    bad_status, good_status, worktree_rows = asyncio.run(_run())
    assert bad_status == "Bad unavailable: provider failed"
    assert good_status.startswith("1 good")
    assert worktree_rows == len(_FIX)


def test_contributed_pivot_keeps_cached_rows_when_refresh_fails():
    contribution = _contribution("Tasks")
    calls = 0

    def loader(pivot, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return PivotPayload(rows=({"id": "1", "title": "cached"},), summary={})
        raise PivotLoadError("refresh failed")

    async def _run() -> tuple[str, int]:
        app = picker_app.WorktreeManagerApp(
            lambda: list(_FIX),
            project="r",
            contributions=[contribution],
            pivot_loader=loader,
        )
        async with app.run_test(size=(100, 24)) as pilot:
            await app.workers.wait_for_complete()
            from textual.widgets import DataTable, Tabs
            app.query_one(Tabs).active = "pivot-1"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("r")
            await app.workers.wait_for_complete()
            return app._last_status, app.query_one(DataTable).row_count

    status, row_count = asyncio.run(_run())
    assert status == "Tasks unavailable: refresh failed · showing 1 cached"
    assert row_count == 1


def test_unavailable_contributed_pivot_stays_visible_and_isolated():
    contribution = replace(_contribution("Tasks"), command_available=False)

    async def _run() -> tuple[str, int]:
        app = picker_app.WorktreeManagerApp(
            lambda: list(_FIX),
            project="r",
            contributions=[contribution],
        )
        async with app.run_test(size=(100, 24)) as pilot:
            await app.workers.wait_for_complete()
            from textual.widgets import DataTable, Tabs
            tabs = app.query_one(Tabs)
            tabs.active = "pivot-1"
            await pilot.pause()
            unavailable = app._last_status
            tabs.active = "pivot-0"
            await pilot.pause()
            return unavailable, app.query_one(DataTable).row_count

    status, worktree_rows = asyncio.run(_run())
    assert status == "Tasks unavailable: agent-example is not available on PATH"
    assert worktree_rows == len(_FIX)


# ── launch/resume action (slice 3) ────────────────────────────────────────────

_FIX = [
    Worktree(id="aaaa1111", repo="r", machine="m", branch="b", title="first",
             state="wip", ahead=1, behind=0, dirty=False, status="active",
             path="/x", raw={}),
    Worktree(id="bbbb2222", repo="r", machine="m", branch="b2", title="second",
             state="clean", ahead=0, behind=0, dirty=False, status="active",
             path="/y", raw={}),
]


def _drive(keys: list[str]):
    async def _run():
        app = picker_app.WorktreeManagerApp(lambda: list(_FIX), project="r")
        async with app.run_test(size=(100, 24)) as pilot:
            await app.workers.wait_for_complete()
            for k in keys:
                await pilot.press(k)
        return app.pending_launch

    return asyncio.run(_run())


def test_launch_key_requests_resume_of_selected_row():
    req = _drive(["l"])
    assert req is not None
    assert req.mode == "resume"
    assert req.project == "r"
    assert req.worktree_id == "aaaa1111"  # cursor starts on the first row


def test_cursor_move_then_launch_targets_that_row():
    req = _drive(["down", "l"])
    assert req is not None and req.worktree_id == "bbbb2222"


def test_bare_resume_key():
    req = _drive(["b"])
    assert req is not None and req.mode == "bare-resume"


def test_new_worktree_key_needs_no_selection():
    req = _drive(["n"])
    assert req is not None and req.mode == "new" and req.worktree_id is None


def test_run_picker_invokes_on_launch(monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, *a, **kw):
            self.pending_launch = picker_app.LaunchRequest(
                project="r", worktree_id="aaaa1111", mode="resume")

        def run(self):
            return None

    monkeypatch.setattr(picker_app, "WorktreeManagerApp", _FakeApp)

    def on_launch(req):
        captured["req"] = req
        return 42

    code = picker_app.run_picker(lambda: [], project="r", on_launch=on_launch)
    assert code == 42
    assert captured["req"].worktree_id == "aaaa1111"


def test_demo_engine_resolve_emits_plan():
    rc, out = _run_demo_engine(
        ["--project", demo.DEMO_PROJECT, "resolve", "--json",
         "--worktree-id", "aperture-labs-testchamber-18c4"])
    assert rc == 0
    plan = json.loads(out)
    assert plan["action"] == "exec"
    assert plan["worktree_id"] == "aperture-labs-testchamber-18c4"
    assert "Aperture" in " ".join(plan["cmd"])


def test_demo_engine_resolve_new():
    rc, out = _run_demo_engine(
        ["--project", demo.DEMO_PROJECT, "resolve", "--json", "--new"])
    assert rc == 0
    assert "creating" in json.loads(out)["cmd"][-1]
