"""History navigation only for launch prompts, never steering fields."""

from __future__ import annotations

from collections.abc import Iterable

from .. import context
from ...seed_history import HistoryCursor, SeedHistory, SeedHistoryError
from .field_widgets import _AutoExpandTextArea


class SeedPromptField(_AutoExpandTextArea):
    def __init__(self, *, history_on_mount=True) -> None:
        super().__init__(id="q-0")
        self.seed_store: SeedHistory | None = None
        self.seed_cursor = HistoryCursor([])
        self._history_on_mount = history_on_mount

    def on_mount(self) -> None:
        super().on_mount()
        if not self._history_on_mount:
            return
        try:
            self.seed_store = SeedHistory(context.project())
            self.seed_cursor = self.seed_store.cursor()
        except (SeedHistoryError, RuntimeError) as exc:
            self.notify(str(exc), severity="error")

    def refusal(
        self, *, options: Iterable[str] = (), refusal_reason: str | None = None,
    ) -> str | None:
        """Validate a snapshot without persisting or changing the draft."""
        unsupported = {"Anchor repo", "Bare", "AHP", "Local model"} & set(options)
        if unsupported and not refusal_reason:
            refusal_reason = (
                "Prompt launch is unsupported with " + ", ".join(sorted(unsupported))
                + "; clear the prompt or change the options."
            )
        return refusal_reason if self.text.strip() else None

    def remember(
        self, *, options: Iterable[str] = (), refusal_reason: str | None = None,
    ) -> bool:
        if not self.text.strip():
            return True
        reason = self.refusal(options=options, refusal_reason=refusal_reason)
        if reason:
            self.notify(reason, severity="error")
            self.focus()
            return False
        try:
            if self.seed_store is None:
                self.seed_store = SeedHistory(context.project())
            self.seed_store.remember(self.text)
        except (SeedHistoryError, RuntimeError) as exc:
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
            try:
                text = self.seed_cursor.move(self.text, 1 if older else -1)
            except SeedHistoryError as exc:
                event.prevent_default()
                event.stop()
                self.notify(str(exc), severity="error")
                return
            if text is not None:
                event.prevent_default()
                event.stop()
                self.load_text(text)
                self.move_cursor(
                    (0, 0) if older else (self.document.line_count - 1, 0)
                )
                return
        super().on_key(event)


def compose_seed_prompt(*, history_on_mount=True):
    widget = SeedPromptField(history_on_mount=history_on_mount)
    return [widget], {"primary": widget}
