"""The Worktree Manager Picker — a Textual UI over the engine-client boundary.

This is the interactive front-end the bare-invocation seam hands off to (the
out-of-plugin Picker the plugin's ``picker_tui`` is being retired in favour of).
It reaches worktree data **only** through :mod:`engine_client` (which shells out
to ``agent-worktrees --json``); it owns no worktree logic or state, and imports
nothing from the plugin — the process boundary that keeps the coupling one-way.

The UI takes an **injected source** (``Callable[[], list[Worktree]]``) so it can
render live engine data, a fake/demo engine (Aperture Labs), or a fixture in a
test, all identically. A headless :func:`capture_svg` renders a screenshot with
no terminal for demos and golden checks.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from . import demo
from . import engine_client as ec
from .engine_client import EngineError, Worktree

Source = Callable[[], "list[Worktree]"]

_COLUMNS = ("id", "machine", "repo", "state", "±sync", "title")


def _state_cell(w: Worktree) -> str:
    state = (w.state or "-").upper()
    if w.dirty and "DIRTY" not in state:
        state = f"{state} DIRTY" if state != "-" else "DIRTY"
    return state


def rows_to_text(worktrees: list[Worktree], *, project: str) -> str:
    """A plain-text table of the same rows the Picker shows (preview + tests)."""
    header = f"Worktree Manager — {project}  ({len(worktrees)} worktrees)"
    lines = [header, "-" * len(header)]
    for w in worktrees:
        lines.append(
            f"  {w.id4}  {(w.machine or '?').ljust(13)}  "
            f"{(w.repo or '?').ljust(18)}  {_state_cell(w).ljust(12)}  "
            f"{(w.sync_tag or '').ljust(6)}  {w.title or ''}")
    return "\n".join(lines) + "\n"


class WorktreeManagerApp(App):
    """A minimal, real Picker: header, a worktree table, refresh + quit."""

    TITLE = "Worktree Manager"
    CSS = """
    Screen { layout: vertical; }
    #status { color: $text-muted; padding: 0 1; height: auto; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, source: Source, *, project: str,
                 subtitle: str | None = None) -> None:
        super().__init__()
        self._source = source
        self._project = project
        self._last_status = ""
        self.sub_title = subtitle or project

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="status")
        table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
        table.add_columns(*_COLUMNS)
        yield table
        yield Footer()

    def on_mount(self) -> None:
        self._reload()

    def action_refresh(self) -> None:
        self._reload()

    def _reload(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        status = self.query_one("#status", Static)
        try:
            worktrees = self._source()
        except EngineError as e:
            hint = ("  Run `worktree-manager setup --apply` to install it."
                    if e.install_hint else "")
            self._last_status = f"engine unavailable: {e}{hint}"
            status.update(self._last_status)
            return
        self._last_status = (f"{self._project} · {len(worktrees)} worktree(s) · "
                             f"r: refresh · q: quit")
        status.update(self._last_status)
        for w in worktrees:
            table.add_row(w.id4, w.machine or "?", w.repo or "?",
                          _state_cell(w), w.sync_tag or "", w.title or "")


# ── sources ─────────────────────────────────────────────────────────────────

def engine_source(project: str) -> Source:
    """A source that lists a project's worktrees via the real engine."""
    return lambda: ec.list_worktrees(project)


def demo_source() -> Source:
    """A source backed by the bundled Aperture Labs fake engine.

    Routes through ``engine_client`` + a subprocess to the fake engine, so the
    demo exercises the exact render path (spawn → JSON → dataclass) the real
    engine uses — the process boundary is never bypassed.
    """
    ec.set_engine_command([sys.executable, "-m", "worktree_manager.demo_engine"])
    return lambda: ec.list_worktrees(demo.DEMO_PROJECT)


# ── headless capture ─────────────────────────────────────────────────────────

async def _render_svg(app: WorktreeManagerApp, size: tuple[int, int]) -> str:
    async with app.run_test(size=size):
        await app.workers.wait_for_complete()
        return app.export_screenshot()


def capture_svg(source: Source, *, project: str, size: tuple[int, int] = (110, 32),
                subtitle: str | None = None) -> str:
    """Render the Picker headlessly and return an SVG screenshot string."""
    app = WorktreeManagerApp(source, project=project, subtitle=subtitle)
    return asyncio.run(_render_svg(app, size))


def run_picker(source: Source, *, project: str, subtitle: str | None = None) -> int:
    """Launch the interactive Picker (blocks until the user quits)."""
    WorktreeManagerApp(source, project=project, subtitle=subtitle).run()
    return 0
