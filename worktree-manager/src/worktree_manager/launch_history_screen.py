"""Bounded local launch-history navigation, with private input explicitly opted in."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from .launch_attempts import MANAGER_OUTCOMES, LaunchAttemptError, LaunchAttempts
from .launch_history_cli import read_detail
from .production_picker.picker_tui.background import run_background
from .production_picker.picker_tui.inbox import Inbox

log = logging.getLogger(__name__)
_PAGE_SIZE = 25
_DISPLAY_LIMIT = 32768


class LaunchHistoryScreen(ModalScreen[None]):
    CSS = """
    LaunchHistoryScreen { padding: 1 2; background: $surface; }
    LaunchHistoryScreen Horizontal { height: auto; }
    LaunchHistoryScreen Input { width: 1fr; }
    LaunchHistoryScreen DataTable { height: 1fr; }
    LaunchHistoryScreen VerticalScroll { height: 1fr; }
    LaunchHistoryScreen Static { height: auto; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", show=False),
        Binding("r", "refresh", show=False),
    ]

    def __init__(self, project: str = "") -> None:
        super().__init__()
        self.project = project
        self._store = LaunchAttempts()
        self._inbox = Inbox(self)
        self._threads: set[threading.Thread] = set()
        self._cancel = threading.Event()
        self._generation = object()
        self._busy = False
        self._before: tuple[float, str] | None = None
        self._previous: list[tuple[float, str] | None] = []
        self._filters: tuple[str | None, ...] | None = None
        self._rows: list[dict] = []
        self._has_more = False
        self._detail: dict | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "Local launch history / read-only\n"
            "Metadata only by default. Detail queries the local project engine, not the selected remote venue. "
            "Manager outcomes are boundary observations, not Copilot success or prompt acceptance.",
            markup=False,
        )
        with Horizontal(id="history-filters"):
            yield Input(value=self.project, placeholder="Project (blank: all)", id="history-project")
            yield Input(placeholder="Action (blank: all)", id="history-action")
            yield Input(
                placeholder="Outcome: unknown/refused/failed/interrupted/returned",
                id="history-outcome",
            )
            yield Button("Filter / refresh", id="history-refresh")
        yield Static("Reading saved metadata...", id="history-status", markup=False)
        yield DataTable(id="history-list", cursor_type="row")
        with VerticalScroll(id="history-detail-scroll"):
            yield Static("", id="history-detail", markup=False)
        with Horizontal():
            yield Button("Newer", id="history-newer", disabled=True)
            yield Button("Older", id="history-older", disabled=True)
            yield Button("Back to list", id="history-back", disabled=True)
            yield Button("Reveal private input", id="history-input", disabled=True)
            yield Button("Close", id="history-close")

    def on_mount(self) -> None:
        self.query_one("#history-list", DataTable).add_columns(
            "Confirmed (UTC)", "Action", "Project", "Target", "Manager outcome", "Attempt",
        )
        self.query_one("#history-detail-scroll").display = False
        self._load_list()

    def _buttons(self) -> None:
        detail = self._detail is not None
        for key, enabled in (
            ("refresh", not detail),
            ("newer", not detail and bool(self._previous)),
            ("older", not detail and self._has_more),
            ("back", detail),
            ("input", detail and self._detail["attempt"]["inputs_available"]
             and "input" not in self._detail["attempt"]),
        ):
            button = self.query_one("#history-" + key, Button)
            button.disabled = self._busy or not enabled
            button.display = detail if key in ("back", "input") else not detail
        self.query_one("#history-list", DataTable).disabled = self._busy

    def _read(self, work, done) -> None:
        self._busy = True
        self._buttons()
        generation = self._generation = object()

        def guarded():
            try:
                return True, work()
            except LaunchAttemptError:
                return False, "Protected launch history is unavailable or input has expired."
            except Exception as error:
                log.error("Launch history read failed (%s).", type(error).__name__)
                return False, "Launch history could not be read; no launch was attempted."

        def complete(result):
            if generation is not self._generation or not self.is_mounted:
                return
            self._busy = False
            if result is not None and result[0]:
                done(result[1])
            else:
                self.query_one("#history-status", Static).update(
                    result[1] if result is not None else "Launch history read failed.",
                )
            self._buttons()

        run_background(
            inbox=self._inbox, bg_threads=self._threads, cancel=self._cancel,
            label="Reading launch history", work=guarded, done=complete, quiet=True,
        )

    def _load_list(self) -> None:
        project = self.query_one("#history-project", Input).value.strip() or None
        action = self.query_one("#history-action", Input).value.strip() or None
        outcome = self.query_one("#history-outcome", Input).value.strip() or None
        if (project, action, outcome) != self._filters:
            self._before, self._previous = None, []
            self._filters = project, action, outcome
        cursor = self._before
        self._rows, self._has_more = [], False
        self.query_one("#history-list", DataTable).clear()
        self.query_one("#history-status", Static).update("Reading saved metadata...")
        if outcome is not None and outcome not in MANAGER_OUTCOMES:
            self.query_one("#history-status", Static).update(
                "Manager outcome must be blank or one of: " + ", ".join(MANAGER_OUTCOMES),
            )
            self._buttons()
            return

        def loaded(rows):
            self._has_more = len(rows) > _PAGE_SIZE
            self._rows = rows[:_PAGE_SIZE]
            table = self.query_one("#history-list", DataTable)
            table.clear()
            for row in self._rows:
                try:
                    confirmed = datetime.fromtimestamp(
                        row["submitted_at"], timezone.utc,
                    ).isoformat(timespec="seconds")
                except (ValueError, OverflowError, OSError):
                    confirmed = f"{row['submitted_at']} (unrenderable UTC)"
                target = " / ".join(str(row[key]) for key in (
                    "machine", "environment", "provider", "venue_id",
                ) if row[key] is not None) or ("local" if row["is_local"] else "unknown")
                table.add_row(
                    *(Text(value) for value in (
                        confirmed,
                        row["action"], row["project"], target, row["manager_outcome"], row["attempt_id"],
                    )),
                    key=row["attempt_id"],
                )
            self.query_one("#history-status", Static).update(
                f"{len(self._rows)} saved attempt(s). Enter a row for bounded detail."
                if self._rows else "No saved attempts match this local query.",
            )
            table.focus()

        self._read(
            lambda: self._store.entries(
                limit=_PAGE_SIZE + 1, project=project, action=action, outcome=outcome,
                before=cursor[0] if cursor else None, before_id=cursor[1] if cursor else None,
            ),
            loaded,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "history-list":
            return
        event.stop()
        if self._busy:
            return
        attempt_id = str(event.row_key.value)
        self.query_one("#history-status", Static).update(
            "Reading local detail; Escape remains available while the engine responds...",
        )

        def loaded(report):
            self._detail = report
            self.query_one("#history-list").display = False
            self.query_one("#history-filters").display = False
            self.query_one("#history-detail-scroll").display = True
            self._render_detail()
            self.query_one("#history-back", Button).focus()

        self._read(
            lambda: read_detail(self._store, attempt_id, event_limit=20, receipt_limit=10),
            loaded,
        )

    def _render_detail(self) -> None:
        text = json.dumps(self._detail, ensure_ascii=False, indent=2)
        if len(text) > _DISPLAY_LIMIT:
            attempt = self._detail["attempt"]
            command = f"worktree-manager launch-attempts {attempt['attempt_id']} --json"
            if "input" in attempt:
                command += " --include-input"
            text = text[:_DISPLAY_LIMIT] + f"\n[Preview truncated; use {command} for detail.]"
        self.query_one("#history-detail", Static).update(text)
        self.query_one("#history-status", Static).update(
            "Local evidence only; prompt acceptance remains unknown. "
            "At most 20 events and 10 receipts; cursors and source errors are retained. "
            "Use the CLI for additional pages.",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choice = event.button.id
        if choice == "history-close":
            self.action_close()
        elif self._busy:
            return
        elif choice == "history-refresh":
            self._before, self._previous = None, []
            self._load_list()
        elif choice == "history-older" and self._has_more:
            self._previous.append(self._before)
            row = self._rows[-1]
            self._before = row["submitted_at"], row["attempt_id"]
            self._load_list()
        elif choice == "history-newer" and self._previous:
            self._before = self._previous.pop()
            self._load_list()
        elif choice == "history-back":
            self._detail = None
            self.query_one("#history-list").display = True
            self.query_one("#history-filters").display = True
            self.query_one("#history-detail-scroll").display = False
            self.query_one("#history-detail", Static).update("")
            self.query_one("#history-list", DataTable).focus()
            self.query_one("#history-status", Static).update("Saved local metadata; Enter a row for detail.")
            self._buttons()
        elif choice == "history-input" and self._detail is not None:
            attempt_id = self._detail["attempt"]["attempt_id"]

            def loaded(detail):
                self._detail["attempt"] = detail
                self._render_detail()

            self._read(
                lambda: self._store.detail(attempt_id, include_input=True, event_limit=20),
                loaded,
            )

    def on_inbox_updated(self) -> None:
        self._inbox.drain_apply()

    def on_unmount(self) -> None:
        self._cancel.set()
        self._generation = object()
        self._detail = None

    def action_close(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        if not self._busy and self._detail is None:
            self._load_list()
