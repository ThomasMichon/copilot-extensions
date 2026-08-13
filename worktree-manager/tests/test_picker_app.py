"""Tests for the demo (fake engine) + the Textual Picker (Phase 6b slice 2).

The Picker reaches data only across the process boundary, so these drive the
bundled fake engine (Aperture Labs) end-to-end -- subprocess spawn, JSON parse,
dataclass mapping, and a headless render/screenshot -- with no live engine.
"""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stdout

import pytest

from worktree_manager import demo, demo_engine
from worktree_manager import engine_client as ec
from worktree_manager import picker_app
from worktree_manager.engine_client import EngineError, Worktree


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


def test_app_engine_error_shows_status_not_crash():
    def boom():
        raise EngineError("nope", install_hint=True)

    async def _run() -> str:
        app = picker_app.WorktreeManagerApp(boom, project="r")
        async with app.run_test(size=(100, 24)):
            return app._last_status

    assert "engine unavailable" in asyncio.run(_run())
