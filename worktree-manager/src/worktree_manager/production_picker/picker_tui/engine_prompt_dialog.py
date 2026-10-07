#!/usr/bin/env python3
"""``PromptDlgScreen`` -- the lean "Resume prompt…" composer dialog, split
into its own module to keep ``engine_dialogs.py`` under the repo's per-module
line-count cap (resume-prompt-durable-seed-and-mux-fix Phase 2)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .engine_focus import FocusGroup
from .field_widgets import compose_field

class PromptDlgScreen(ModalScreen[bool]):
    """Lean modal prompt composer ("Resume prompt…"): a free-text field plus
    ``[Confirm] [Cancel]`` -- ``ScopeDlgScreen``'s folded-in ``show_prompt``
    field, minus its option list (Resume has no equivalent use for it).
    ``dismiss(True)``/``dismiss(False)`` on Confirm/Cancel; the caller reads
    ``self.seed_prompt`` (set on Confirm) after dismissal."""

    CSS = """
    PromptDlgScreen { align: center middle; background: $background 55%; }
    PromptDlgScreen > #prompt-frame {
        width: 68; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    PromptDlgScreen #prompt-hint { color: grey; height: auto; padding: 0 0 1 0; }
    PromptDlgScreen TextArea { border: round grey; background: $surface; }
    PromptDlgScreen #prompt-buttons { width: 1fr; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, title: str, hint: str) -> None:
        super().__init__()
        self._title = title
        self._hint = hint
        self.seed_prompt = ""            # set on Confirm
        self._prompt_widgets, self._prompt_rec = compose_field(
            {"name": "prompt", "type": "textarea"}, 0)

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-frame"):
            yield Static(self._hint, id="prompt-hint")
            yield from self._prompt_widgets
            yield FocusGroup(
                [("confirm", "Confirm"), ("cancel", "Cancel")],
                id="prompt-buttons")

    def on_mount(self) -> None:
        self.query_one("#prompt-frame", Vertical).border_title = self._title
        self._prompt_rec["primary"].focus()

    def _advance_focus(self, widget) -> None:
        """Enter from the prompt box jumps to Confirm (nothing else to Tab
        past in this dialog)."""
        try:
            group = self.query_one("#prompt-buttons", FocusGroup)
            group._idx = 0
            group.focus()
        except Exception:
            pass

    def _collect_prompt(self) -> str:
        return str(self._prompt_rec["primary"].text).strip()

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        confirmed = event.value == "confirm"
        if confirmed:
            self.seed_prompt = self._collect_prompt()
        self.dismiss(confirmed)

    def action_cancel(self) -> None:
        self.dismiss(False)
