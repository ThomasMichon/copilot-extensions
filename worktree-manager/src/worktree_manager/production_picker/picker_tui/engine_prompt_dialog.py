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
from .seed_prompt_field import compose_seed_prompt

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

    def __init__(
        self, title: str, hint: str, *, seed_refusal: str | None = None,
        launch_decision: dict | None = None, journal_enabled: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._hint = hint
        self._seed_refusal = seed_refusal
        self._launch_decision = launch_decision or {"action": "resume", "is_local": True}
        self._journal_enabled = journal_enabled
        self.attempt_id: str | None = None
        self.launch_decision: dict | None = None
        self.seed_prompt = ""            # set on Confirm
        self._prompt_widgets, self._prompt_rec = compose_seed_prompt()

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-frame"):
            yield Static(
                self._hint + "\nUp/Down: seed history at text boundaries; Ctrl+Up/Down: anywhere.",
                id="prompt-hint",
            )
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
            from copy import deepcopy
            from ...launch_confirmation import confirm_before_effect
            from .. import context

            field = self._prompt_rec["primary"]
            decision = deepcopy(self._launch_decision)
            decision.setdefault("options", {})["seed_prompt"] = self._collect_prompt()

            def saved(result):
                self.attempt_id = result.get("attempt_id")
                self.launch_decision = result
                self.seed_prompt = result["options"]["seed_prompt"]
                self.dismiss(True)

            confirm_before_effect(
                self, context.project(), decision, surface="resume-prompt", saved=saved,
                prompt=str(field.text), refusal=field.refusal(refusal_reason=self._seed_refusal),
                history=field.seed_store,
                enabled=self._journal_enabled,
            )
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if self.app.screen is self:
            self.dismiss(False)
