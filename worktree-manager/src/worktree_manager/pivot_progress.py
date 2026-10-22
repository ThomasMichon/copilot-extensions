"""Shared nonblocking progress modal for explicitly activated view actions."""
from __future__ import annotations

import asyncio
import threading

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PivotProgressScreen(ModalScreen):
    CSS = """
    PivotProgressScreen { align: center middle; background: $background 55%; }
    PivotProgressScreen > Vertical {
        width: 76; height: auto; max-height: 90%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    PivotProgressScreen Static { height: auto; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, runtime, action, context, *, completed, close_runtime=False):
        super().__init__()
        self.runtime = runtime
        self.action = action
        self.context = dict(context)
        self.completed = completed
        self.close_runtime = close_runtime
        self.cancelled = threading.Event()
        self.finished = False

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical():
            yield Static(self.action.label, markup=False)
            yield Static("Starting...", id="view-progress", markup=False)
            yield Button("Cancel", id="view-cancel")

    def on_mount(self):
        self.run_worker(self._run())

    async def _run(self):
        loop = asyncio.get_running_loop()

        def progress(_pct, message):
            loop.call_soon_threadsafe(self._message, message)

        try:
            ok, message = await asyncio.to_thread(
                self.runtime.run_action_stream, self.action, self.context, progress,
                should_cancel=self.cancelled.is_set,
            )
        except (OSError, RuntimeError) as exc:
            ok, message = False, f"Action failed: {exc}"
        if not self.is_mounted:
            return
        self.finished = True
        self._message(
            ("Cancelled." if self.cancelled.is_set()
             else ("Succeeded: " if ok else "Failed: ") + message)
        )
        self.query_one("#view-cancel", Button).label = "Close"
        if ok and not self.cancelled.is_set():
            self.completed()

    def _message(self, message):
        if self.is_mounted:
            self.query_one("#view-progress", Static).update(message)

    def action_cancel(self):
        if self.finished:
            self.dismiss()
        else:
            self.cancelled.set()
            self._message("Cancelling authorization...")

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.action_cancel()

    def on_unmount(self):
        self.cancelled.set()
        if self.close_runtime:
            self.runtime.close()
