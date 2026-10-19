"""History navigation only for launch prompts, never steering fields."""

from __future__ import annotations

from collections.abc import Iterable

from .. import context
from ...seed_history import HistoryCursor, SeedHistory, SeedHistoryError
from .field_widgets import _AutoExpandTextArea


class SeedPromptField(_AutoExpandTextArea):
    def __init__(self) -> None:
        super().__init__(id="q-0")
        self.seed_store: SeedHistory | None = None
        self.seed_cursor = HistoryCursor([])

    def on_mount(self) -> None:
        super().on_mount()
        self.seed_store = SeedHistory(context.project())
        try:
            self.seed_cursor = HistoryCursor(self.seed_store.entries())
        except SeedHistoryError as exc:
            self.notify(str(exc), severity="error")

    def remember(
        self, *, options: Iterable[str] = (), refusal_reason: str | None = None,
    ) -> bool:
        unsupported = {"Anchor repo", "Bare", "AHP", "Local model"} & set(options)
        if unsupported and not refusal_reason:
            refusal_reason = (
                "Prompt launch is unsupported with " + ", ".join(sorted(unsupported))
                + "; clear the prompt or change the options."
            )
        if refusal_reason and self.text.strip():
            self.notify(refusal_reason, severity="error")
            self.focus()
            return False
        try:
            if self.seed_store is None:
                self.seed_store = SeedHistory(context.project())
            self.seed_store.remember(self.text)
        except SeedHistoryError as exc:
            self.notify(str(exc), severity="error")
            self.focus()
            return False
        return True

    def on_key(self, event) -> None:
        row, _column = self.cursor_location
        older = event.key == "ctrl+up" or (
            event.key == "up" and row == 0
        )
        newer = event.key == "ctrl+down" or (
            event.key == "down" and row == self.document.line_count - 1
        )
        if older or newer:
            text = self.seed_cursor.move(self.text, 1 if older else -1)
            if text is not None:
                event.prevent_default()
                event.stop()
                self.load_text(text)
                self.move_cursor(
                    (0, 0) if older else (self.document.line_count - 1, 0)
                )
                return
        super().on_key(event)


def compose_seed_prompt():
    widget = SeedPromptField()
    return [widget], {"primary": widget}
