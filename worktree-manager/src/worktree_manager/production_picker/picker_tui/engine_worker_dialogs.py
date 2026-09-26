"""``WorkerMessageScreen`` -- type a message for a supervised remote worker.

A small modal (venue-pivots-ux ``supervised-worker-actions`` / Send message):
the operator types free text and chooses how it lands -- **steer** it into the
worker's running turn (Ctrl+S) or **interrupt** the turn with it (Ctrl+X).
Escape cancels. Dismisses with ``(delivery, text)`` or ``None``.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea


class WorkerMessageScreen(ModalScreen[tuple]):
    DEFAULT_CSS = """
    WorkerMessageScreen { align: center middle; }
    WorkerMessageScreen #wm-box {
        width: 80%; max-width: 110; height: auto; max-height: 80%;
        border: round #4aa3ff; background: $surface; padding: 0 1;
    }
    WorkerMessageScreen #wm-title { color: #ffaf00; height: auto; padding: 1 0 0 0; }
    WorkerMessageScreen TextArea { height: 8; border: round grey; background: $surface; }
    WorkerMessageScreen #wm-foot { color: grey; height: auto; padding: 0 0 1 0; }
    """
    # priority: the TextArea binds Ctrl+X (cut) itself; the send keys must win.
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+s", "send('steer')", show=False, priority=True),
        Binding("ctrl+x", "send('interrupt')", show=False, priority=True),
    ]

    def __init__(self, target: str) -> None:
        super().__init__()
        self._target = target or "worker"

    def compose(self) -> ComposeResult:
        with Vertical(id="wm-box"):
            yield Static(f"Message to {self._target}", id="wm-title")
            yield TextArea(id="wm-text")
            yield Static(
                "Ctrl+S steer the running turn · Ctrl+X interrupt with this · Esc cancel",
                id="wm-foot",
            )

    def on_mount(self) -> None:
        self.query_one("#wm-text", TextArea).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_send(self, delivery: str) -> None:
        text = self.query_one("#wm-text", TextArea).text.strip()
        if not text:
            self.query_one("#wm-foot", Static).update(
                "Type a message first · Ctrl+S steer · Ctrl+X interrupt · Esc cancel")
            return
        self.dismiss((delivery, text))
