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
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static, Tab, Tabs

from . import demo
from . import engine_client as ec
from .engine_client import EngineError, Worktree
from .pivot_runtime import PivotLoadError, PivotPayload, load_pivot
from .plugin_contracts import PivotContract, PluginContribution

Source = Callable[[], "list[Worktree]"]
ContextSource = Callable[[], "Mapping[str, object]"]
PivotLoader = Callable[[PivotContract, Mapping[str, object]], PivotPayload]

_COLUMNS = ("id", "machine", "repo", "state", "±sync", "title")
_WORKTREES = "worktrees"


@dataclass(frozen=True)
class _PivotDescriptor:
    key: str
    label: str
    contribution: PluginContribution | None = None


@dataclass
class _ViewState:
    status: str = "idle"
    rows: list[object] = field(default_factory=list)
    summary: dict[str, object] = field(default_factory=dict)
    error: str = ""
    install_hint: bool = False
    generation: int = 0


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
    """A Picker over worktrees plus Manager-owned plugin contributions."""

    TITLE = "Worktree Manager"
    CSS = """
    Screen { layout: vertical; }
    Tabs { height: 3; }
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

    def __init__(
        self,
        source: Source,
        *,
        project: str,
        subtitle: str | None = None,
        contributions: Sequence[PluginContribution] = (),
        context_source: ContextSource | None = None,
        pivot_loader: PivotLoader = load_pivot,
    ) -> None:
        super().__init__()
        self._source = source
        self._project = project
        self._context_source = context_source or (
            lambda: {"project": project, "machine": ""}
        )
        self._pivot_loader = pivot_loader
        self._pivots = self._build_pivots(contributions)
        self._pivot_by_tab = {
            f"pivot-{index}": pivot for index, pivot in enumerate(self._pivots)
        }
        home_contribution = next(
            (
                contribution for contribution in contributions
                if contribution.pivot is not None
                and contribution.pivot.home
                and contribution.command_available
            ),
            None,
        )
        self._initial_pivot = next(
            (
                pivot for pivot in self._pivots
                if pivot.contribution is home_contribution
            ),
            self._pivots[0],
        )
        self._active_key = self._initial_pivot.key
        self._states = {pivot.key: _ViewState() for pivot in self._pivots}
        self._last_status = ""
        self._worktrees: list[Worktree] = []
        #: Set when the operator picks a launch; read by the runner after quit.
        self.pending_launch: LaunchRequest | None = None
        self.sub_title = subtitle or project

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Tabs(
            *(
                Tab(pivot.label, id=f"pivot-{index}")
                for index, pivot in enumerate(self._pivots)
            ),
            id="pivots",
        )
        yield Static("", id="status")
        table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
        yield table
        yield Footer()

    def on_mount(self) -> None:
        initial_tab = next(
            tab_id for tab_id, pivot in self._pivot_by_tab.items()
            if pivot.key == self._initial_pivot.key
        )
        self.query_one(Tabs).active = initial_tab
        self._activate(self._initial_pivot)
        self.query_one(DataTable).focus()

    @staticmethod
    def _build_pivots(
        contributions: Sequence[PluginContribution],
    ) -> list[_PivotDescriptor]:
        worktrees = _PivotDescriptor(key=_WORKTREES, label="Worktrees")
        contributed: list[_PivotDescriptor] = []
        for index, contribution in enumerate(contributions):
            pivot = contribution.pivot
            if pivot is None:
                continue
            contributed.append(_PivotDescriptor(
                key=f"contribution-{index}",
                label=pivot.label,
                contribution=contribution,
            ))

        all_descriptors = [worktrees, *contributed]
        by_label: dict[str, _PivotDescriptor] = {}
        for descriptor in all_descriptors:
            by_label.setdefault(descriptor.label.casefold(), descriptor)

        fallback = {
            descriptor.key
            for descriptor in contributed
            if descriptor.contribution.pivot.after.casefold() not in by_label
        }
        # A pivot anchored to an unresolved pivot belongs in the same fallback
        # tail, rather than jumping ahead of its missing parent.
        changed = True
        while changed:
            changed = False
            for descriptor in contributed:
                if descriptor.key in fallback:
                    continue
                anchor = by_label[
                    descriptor.contribution.pivot.after.casefold()
                ]
                if anchor.key in fallback:
                    fallback.add(descriptor.key)
                    changed = True

        ordered_candidates = [
            descriptor for descriptor in contributed
            if descriptor.key not in fallback
        ]
        nodes = [worktrees, *ordered_candidates]
        edges = {descriptor.key: set() for descriptor in nodes}
        indegree = {descriptor.key: 0 for descriptor in nodes}

        def add_edge(before: _PivotDescriptor, after: _PivotDescriptor) -> None:
            if after.key not in edges[before.key]:
                edges[before.key].add(after.key)
                indegree[after.key] += 1

        siblings: dict[str, _PivotDescriptor] = {}
        for descriptor in ordered_candidates:
            add_edge(worktrees, descriptor)
            anchor_label = descriptor.contribution.pivot.after.casefold()
            anchor = by_label[anchor_label]
            add_edge(anchor, descriptor)
            if prior := siblings.get(anchor_label):
                add_edge(prior, descriptor)
            siblings[anchor_label] = descriptor

        position = {
            descriptor.key: index for index, descriptor in enumerate(nodes)
        }
        by_key = {descriptor.key: descriptor for descriptor in nodes}
        ready = [key for key, degree in indegree.items() if degree == 0]
        ordered: list[_PivotDescriptor] = []
        while ready:
            ready.sort(key=position.__getitem__)
            key = ready.pop(0)
            ordered.append(by_key[key])
            for child in edges[key]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

        # Cycles cannot satisfy `after`; keep those pivots usable at the tail.
        emitted = {descriptor.key for descriptor in ordered}
        ordered.extend(
            descriptor for descriptor in ordered_candidates
            if descriptor.key not in emitted
        )
        ordered.extend(
            descriptor for descriptor in contributed
            if descriptor.key in fallback
        )
        return ordered

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab.id and event.tab.id in self._pivot_by_tab:
            self._activate(self._pivot_by_tab[event.tab.id])

    def action_refresh(self) -> None:
        self._start_load(self._active_pivot(), force=True)

    def _active_pivot(self) -> _PivotDescriptor:
        return next(pivot for pivot in self._pivots if pivot.key == self._active_key)

    def _activate(self, pivot: _PivotDescriptor) -> None:
        self._active_key = pivot.key
        self._configure_table(pivot)
        self._render(pivot)
        self._start_load(pivot)

    def _configure_table(self, descriptor: _PivotDescriptor) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        if descriptor.key == _WORKTREES:
            table.add_columns(*_COLUMNS)
            return
        pivot = descriptor.contribution.pivot  # type: ignore[union-attr]
        if pivot.columns:
            for column in pivot.columns:
                table.add_column(column.header, width=column.width)
        else:
            table.add_columns("id", "title", "details")

    def _start_load(self, descriptor: _PivotDescriptor, *, force: bool = False) -> None:
        state = self._states[descriptor.key]
        if state.status == "loading" or (state.status != "idle" and not force):
            return
        contribution = descriptor.contribution
        if contribution is not None and not contribution.command_available:
            state.status = "error"
            state.error = (
                f"{contribution.pivot.list_cmd[0]} is not available on PATH"
                if contribution.pivot else "list command is unavailable"
            )
            self._render(descriptor)
            return
        state.generation += 1
        generation = state.generation
        state.status = "loading"
        state.error = ""
        state.install_hint = False
        self._render(descriptor)
        self.run_worker(
            self._load(descriptor, generation),
            name=f"load-{descriptor.key}",
        )

    async def _load(self, descriptor: _PivotDescriptor, generation: int) -> None:
        try:
            if descriptor.key == _WORKTREES:
                rows: list[object] = list(await asyncio.to_thread(self._source))
                summary: dict[str, object] = {}
            else:
                contribution = descriptor.contribution
                assert contribution is not None and contribution.pivot is not None
                context = dict(await asyncio.to_thread(self._context_source))
                context.setdefault("project", self._project)
                payload = await asyncio.to_thread(
                    self._pivot_loader, contribution.pivot, context
                )
                rows = list(payload.rows)
                summary = dict(payload.summary)
        except (EngineError, PivotLoadError) as error:
            state = self._states[descriptor.key]
            if state.generation != generation:
                return
            state.status = "error"
            state.error = str(error)
            state.install_hint = isinstance(error, EngineError) and error.install_hint
            if self._active_key == descriptor.key:
                self._render(descriptor)
            return

        state = self._states[descriptor.key]
        if state.generation != generation:
            return
        state.status = "ready"
        state.rows = rows
        state.summary = summary
        state.error = ""
        state.install_hint = False
        if descriptor.key == _WORKTREES:
            self._worktrees = [row for row in rows if isinstance(row, Worktree)]
        if self._active_key == descriptor.key:
            self._render(descriptor)

    def _render(self, descriptor: _PivotDescriptor) -> None:
        if self._active_key != descriptor.key:
            return
        state = self._states[descriptor.key]
        table = self.query_one(DataTable)
        table.clear()
        if descriptor.key == _WORKTREES:
            for row in state.rows:
                if not isinstance(row, Worktree):
                    continue
                table.add_row(
                    row.id4,
                    row.machine or "?",
                    row.repo or "?",
                    _state_cell(row),
                    row.sync_tag or "",
                    row.title or "",
                )
        else:
            self._render_contribution_rows(table, descriptor, state)
        self._set_status(self._status_text(descriptor, state))

    def _render_contribution_rows(
        self,
        table: DataTable,
        descriptor: _PivotDescriptor,
        state: _ViewState,
    ) -> None:
        pivot = descriptor.contribution.pivot  # type: ignore[union-attr]
        for raw in state.rows:
            if not isinstance(raw, Mapping):
                continue
            if pivot.columns:
                table.add_row(*(_cell_text(raw.get(column.key)) for column in pivot.columns))
                continue
            badges: list[str] = []
            for field_name in pivot.badge_fields:
                value = raw.get(field_name)
                if isinstance(value, (list, tuple)):
                    badges.extend(str(item) for item in value)
                elif value not in (None, ""):
                    badges.append(str(value))
            subtitle = raw.get(pivot.subtitle_field) if pivot.subtitle_field else None
            details = " · ".join(
                [*(f"[{badge}]" for badge in badges), *([str(subtitle)] if subtitle else [])]
            )
            table.add_row(
                _cell_text(raw.get(pivot.id_field)),
                _cell_text(raw.get(pivot.title_field)),
                details,
            )

    def _status_text(
        self,
        descriptor: _PivotDescriptor,
        state: _ViewState,
    ) -> str:
        cached = len(state.rows)
        if descriptor.key == _WORKTREES:
            if state.status == "loading":
                return (
                    f"{self._project} · refreshing {cached} cached worktree(s)…"
                    if cached else f"{self._project} · loading worktrees…"
                )
            if state.status == "error":
                hint = (
                    "  Run `worktree-manager setup --apply` to install it."
                    if state.install_hint
                    else ""
                )
                return f"engine unavailable: {state.error}{hint}"
            return (
                f"{self._project} · {cached} worktree(s) · "
                "l: launch/resume · b: bare · n: new · r: refresh · q: quit"
            )

        pivot = descriptor.contribution.pivot  # type: ignore[union-attr]
        if state.status == "loading":
            return (
                f"{pivot.label} · refreshing {cached} cached entr{'y' if cached == 1 else 'ies'}…"
                if cached else f"loading {pivot.label.lower()}…"
            )
        if state.status == "error":
            suffix = f" · showing {cached} cached" if cached else ""
            return f"{pivot.label} unavailable: {state.error}{suffix}"
        if not state.rows:
            return pivot.empty_hint
        summary = _format_summary(pivot, state.summary)
        suffix = f" · {summary}" if summary else ""
        return f"{cached} {pivot.label.lower()}{suffix} · r: refresh · q: quit"

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
        if self._active_key != _WORKTREES:
            self._set_status("pivot actions are not available in this parity slice.")
            return
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


def engine_context_source(project: str) -> ContextSource:
    """Resolve process-boundary context used by contributed argv templates."""
    return lambda: {
        "project": project,
        "machine": ec.get_value(project, "machine"),
    }


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


def capture_svg(
    source: Source,
    *,
    project: str,
    size: tuple[int, int] = (110, 32),
    subtitle: str | None = None,
    contributions: Sequence[PluginContribution] = (),
    context_source: ContextSource | None = None,
) -> str:
    """Render the Picker headlessly and return an SVG screenshot string."""
    app = WorktreeManagerApp(
        source,
        project=project,
        subtitle=subtitle,
        contributions=contributions,
        context_source=context_source,
    )
    return asyncio.run(_render_svg(app, size))


def run_picker(
    source: Source,
    *,
    project: str,
    subtitle: str | None = None,
    on_launch: "Callable[[LaunchRequest], int] | None" = None,
    contributions: Sequence[PluginContribution] = (),
    context_source: ContextSource | None = None,
) -> int:
    """Launch the interactive Picker (blocks until the user quits).

    If the operator picks a launch/resume and ``on_launch`` is provided, the app
    quits and ``on_launch`` runs the composed launch (its exit code is returned).
    Plain quit returns 0. Keeping the exec out of the running app is what lets the
    launch cleanly replace / follow the TUI rather than nest under it.
    """
    app = WorktreeManagerApp(
        source,
        project=project,
        subtitle=subtitle,
        contributions=contributions,
        context_source=context_source,
    )
    app.run()
    if app.pending_launch is not None and on_launch is not None:
        return on_launch(app.pending_launch)
    return 0


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _format_summary(pivot: PivotContract, summary: Mapping[str, object]) -> str:
    if not pivot.summary_template or not summary:
        return ""

    class _Default(dict):
        def __missing__(self, key: str) -> str:
            return ""

    values = _Default({
        key: "" if value is None else str(value)
        for key, value in summary.items()
    })
    try:
        return pivot.summary_template.format_map(values)
    except (KeyError, IndexError, ValueError):
        return pivot.summary_template
