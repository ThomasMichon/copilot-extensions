"""Read-only pending preview followed by explicit, default-Cancel consent."""

import json
from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Static

from worktree_manager.configuration_preview import ConfigurationPreview


class ConfigurationPreviewScreen(Screen[str | None]):
    CSS = """
    ConfigurationPreviewScreen { padding: 1 2; }
    ConfigurationPreviewScreen VerticalScroll { height: 1fr; }
    ConfigurationPreviewScreen Static { height: auto; }
    ConfigurationPreviewScreen Horizontal { height: 3; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", show=False)]

    def __init__(self, label, values):
        super().__init__()
        self.label = label
        self.values = json.dumps(values, ensure_ascii=False, indent=2)
        self.preview: ConfigurationPreview | None = None

    def compose(self):
        yield Static(f"Configuration / {self.label} / Preview", markup=False)
        with VerticalScroll():
            yield Static("Computing provider preview...", id="config-preview", markup=False)
        with Horizontal():
            yield Button("Apply", id="config-preview-apply", disabled=True)
            yield Button("Edit", id="config-preview-edit")
            yield Button("Retry preview", id="config-preview-retry", disabled=True)
            yield Button("Cancel", id="config-preview-cancel")

    def on_mount(self):
        self.query_one("#config-preview-cancel", Button).focus()

    def complete(self, preview: ConfigurationPreview | None, error: str = ""):
        self.preview = preview
        body = f"Preview unavailable\n\n{error}" if error or preview is None else preview.describe()
        self.query_one("#config-preview", Static).update(
            body + "\n\nCollected values:\n" + self.values,
        )
        self.query_one("#config-preview-apply", Button).disabled = (
            preview is None or preview.blocked or bool(error)
        )
        self.query_one("#config-preview-retry", Button).disabled = False
        self.query_one("#config-preview-cancel", Button).focus()

    def on_button_pressed(self, event):
        choice = event.button.id.removeprefix("config-preview-")
        if choice == "apply" and (self.preview is None or self.preview.blocked):
            return
        self.dismiss(None if choice == "cancel" else choice)

    def action_cancel(self):
        self.dismiss(None)
