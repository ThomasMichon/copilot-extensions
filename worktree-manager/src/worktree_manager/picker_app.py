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
from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from . import demo
from . import engine_client as ec
from .engine_client import EngineError, Worktree

Source = Callable[[], "list[Worktree]"]

_COLUMNS = ("id", "machine", "repo", "state", "±sync", "title")


@dataclass(frozen=True)
class LaunchRequest:
    """A launch the operator asked for in the Picker, handed to the runner.

    The Textual app cannot cleanly exec-replace itself, so pressing a launch key
    records this request and quits the app; :func:`run_picker`'s ``on_launch``
    callback then resolves + executes it (fetch the plan across the engine
    boundary, compose by mux capability, run). ``mode`` maps to the engine's
    ``resolve`` selectors: ``resume`` -> ``--worktree-id``, ``bare-resume`` ->
    ``--worktree-id --bare-resume``, ``new`` -> ``--new``.
    """

    project: str
    worktree_id: str | None
    mode: str
    title: str | None = None


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
        ("l", "launch", "Launch/Resume"),
        ("b", "bare_resume", "Bare resume"),
        ("n", "new_worktree", "New worktree"),
    ]

    def __init__(self, source: Source, *, project: str,
                 subtitle: str | None = None) -> None:
        super().__init__()
        self._source = source
        self._project = project
        self._last_status = ""
        self._worktrees: list[Worktree] = []
        #: Set when the operator picks a launch; read by the runner after quit.
        self.pending_launch: LaunchRequest | None = None
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
            self._worktrees = []
            self._last_status = f"engine unavailable: {e}{hint}"
            status.update(self._last_status)
            return
        self._worktrees = list(worktrees)
        self._last_status = (
            f"{self._project} · {len(worktrees)} worktree(s) · "
            f"l: launch/resume · b: bare · n: new · r: refresh · q: quit")
        status.update(self._last_status)
        for w in worktrees:
            table.add_row(w.id4, w.machine or "?", w.repo or "?",
                          _state_cell(w), w.sync_tag or "", w.title or "")

    # ── launch/resume action ─────────────────────────────────────────────────

    def _selected_worktree(self) -> Worktree | None:
        try:
            table = self.query_one(DataTable)
        except Exception:
            return None
        idx = table.cursor_row
        if idx is None or idx < 0 or idx >= len(self._worktrees):
            return None
        return self._worktrees[idx]

    def _request_launch(self, mode: str) -> None:
        """Record the operator's launch choice and quit so the runner can act."""
        if mode == "new":
            self.pending_launch = LaunchRequest(
                project=self._project, worktree_id=None, mode="new")
            self.exit()
            return
        w = self._selected_worktree()
        if w is None:
            self._set_status("no worktree selected — move the cursor to a row first.")
            return
        self.pending_launch = LaunchRequest(
            project=self._project, worktree_id=w.id, mode=mode, title=w.title)
        self.exit()

    def _set_status(self, text: str) -> None:
        self._last_status = text
        try:
            self.query_one("#status", Static).update(text)
        except Exception:
            pass

    def on_data_table_row_selected(self, event) -> None:  # Enter on a row
        self._request_launch("resume")

    def action_launch(self) -> None:
        self._request_launch("resume")

    def action_bare_resume(self) -> None:
        self._request_launch("bare-resume")

    def action_new_worktree(self) -> None:
        self._request_launch("new")


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


def run_picker(source: Source, *, project: str, subtitle: str | None = None,
               on_launch: "Callable[[LaunchRequest], int] | None" = None) -> int:
    """Launch the interactive Picker (blocks until the user quits).

    If the operator picks a launch/resume and ``on_launch`` is provided, the app
    quits and ``on_launch`` runs the composed launch (its exit code is returned).
    Plain quit returns 0. Keeping the exec out of the running app is what lets the
    launch cleanly replace / follow the TUI rather than nest under it.
    """
    app = WorktreeManagerApp(source, project=project, subtitle=subtitle)
    app.run()
    if app.pending_launch is not None and on_launch is not None:
        return on_launch(app.pending_launch)
    return 0
