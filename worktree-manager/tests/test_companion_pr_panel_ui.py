"""Companion PR panel: narrow rendering and dismissal of a blocked real point read."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time

import pytest
from rich.console import Console
from test_companion_pr_state import fact, row, snapshot
from test_companion_topology_ui import pr_data
from textual.widgets import Static, TabbedContent

from worktree_manager import companion_pr_state as cpr
from worktree_manager import mux_companion as companion


@pytest.fixture(autouse=True)
def no_ambient_reads(monkeypatch):
    monkeypatch.setattr(companion.ca, "read_session_attention", lambda *a, **kw: {
        "attention": None, "error": "attention-unavailable",
    })
    monkeypatch.setattr(companion.co, "read_session_observations",
                        lambda *a, **kw: {"observation": None, "error": "none"})


async def _open_graph(app, pilot):
    await app.workers.wait_for_complete()
    app.query_one("#views", TabbedContent).active = "topology-tab"
    await pilot.pause()
    await app.workers.wait_for_complete()


def _narrow_lines(app, width: int) -> list[str]:
    console = Console(width=width, record=True, file=io.StringIO())
    console.print(app.query_one("#topology", Static).content)
    return console.export_text().splitlines()


def _flat(lines: list[str]) -> str:
    """Join wrapped rows, dropping tree guides so wrapped phrases compare whole."""
    text = " ".join(lines)
    for guide in "│├└─":
        text = text.replace(guide, " ")
    return " ".join(text.split())


def _exited(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_narrow_viewport_keeps_pr_controls_and_literal_wrapped_rows(monkeypatch):
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: pr_data())
    monkeypatch.setattr(cpr, "read_associations", lambda wt, **kw: fact(
        row(), row(pr_id="pr-2", number=8, provider="[bold]forge[/]")))
    monkeypatch.setattr(cpr, "point_read", lambda association, **kw: cpr.decode_snapshot(
        snapshot(pr_state="closed", merged=False), association, observed_at=1.0))
    width = 40

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test(size=(width, 30)) as pilot:
            await _open_graph(app, pilot)
            select_widget = app.query_one("#pr-association")
            button = app.query_one("#pr-read-btn")
            for widget in (select_widget, button):
                assert widget.region.width > 0 and widget.region.right <= width
            assert button.region.x >= select_widget.region.right
            select_widget.value = "pr-1"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app._pr_read is not None and not button.disabled
            content = width - 4
            lines = _narrow_lines(app, content)
            assert all(len(line) <= content for line in lines)
            flat = _flat(lines)
            assert "[bold]forge[/]" in flat
            assert "Point read: closed without" in flat
            assert "(record, not current" in flat

    asyncio.run(check())


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal-0 child-exit witness")
@pytest.mark.parametrize("dismissal", ["escape", "refresh", "deselect"])
def test_blocked_real_point_read_is_retired_on_dismissal(tmp_path, monkeypatch, dismissal):
    started = tmp_path / "snapshot.pid"
    script = tmp_path / "blocked_snapshot.py"
    script.write_text(
        "import os,time\n"
        "from pathlib import Path\n"
        f"Path({str(started)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    current = pr_data()
    current.engine_command = [sys.executable, str(script)]
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: current)
    monkeypatch.setattr(cpr, "read_associations", lambda wt, **kw: fact(row()))

    async def run():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await _open_graph(app, pilot)
            app.query_one("#pr-association").value = "pr-1"
            for _ in range(300):
                if started.exists() and started.read_text():
                    break
                await pilot.pause(0.01)
            assert started.exists()
            pid = int(started.read_text())
            assert app._pr_busy and not await asyncio.to_thread(_exited, pid, 0.2)
            if dismissal == "escape":
                await pilot.press("escape")
            elif dismissal == "refresh":
                await pilot.press("r")
            else:
                app.query_one("#pr-association").clear()
                await pilot.pause()
            assert await asyncio.to_thread(_exited, pid, 5)
            if dismissal != "escape":
                await app.workers.wait_for_complete()
                assert app._pr_read is None and not app._pr_busy
                assert app._pr_thread is None or app._pr_thread.done()
        assert not app.is_running and not app._engine_cleanup_failures

    asyncio.run(run())
