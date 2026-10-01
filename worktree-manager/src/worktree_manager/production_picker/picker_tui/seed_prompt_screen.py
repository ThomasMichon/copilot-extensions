"""``SeedPromptScreen`` -- optional first-prompt collector for the "New
worktree…" creation flow (Phase A, ``picker-new-session-prompt-and-composer``).

Deliberately NOT a reuse of ``PivotFormScreen`` (the Steer surface): that
screen's Confirm/Save/Reset button row and on-disk draft persistence are
steer-specific semantics -- there is nothing to save/resume here (a
skipped/cancelled prompt just means an ordinary launch, exactly as before
this screen existed). Reuses only :func:`field_widgets.compose_field` for
the one ``textarea`` field it needs.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .engine_focus import FocusGroup
from .field_widgets import compose_field


class SeedPromptScreen(ModalScreen[str]):
    """Collects one optional free-text prompt right before a new worktree's
    Copilot session launches.

    Always dismisses with a ``str`` -- the collected prompt, or ``""`` when
    skipped/escaped -- never ``None``, so a caller treats the result
    uniformly ("seed this launch with this text" vs. "nothing to seed")
    without a tri-state check.
    """

    CSS = """
    SeedPromptScreen { align: center middle; background: $background 55%; }
    SeedPromptScreen > #seed-frame {
        width: 70%; height: auto; max-height: 80%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    SeedPromptScreen #seed-hint { color: grey; height: auto; padding: 0 0 1 0; }
    SeedPromptScreen TextArea { border: round grey; background: $surface; }
    SeedPromptScreen #seed-buttons { height: 1; margin: 1 0 0 0; padding: 0; }
    """
    BINDINGS = [Binding("escape", "skip", show=False)]

    def __init__(self, *, target: str = "") -> None:
        super().__init__()
        self._target = target
        self._widgets, self._rec = compose_field(
            {"name": "prompt", "type": "textarea"}, 0)

    def compose(self) -> ComposeResult:
        with Vertical(id="seed-frame"):
            yield Static(
                "Optional prompt, queued as this session's first interactive "
                "turn once Copilot is ready -- \"fire and forget\" instead of "
                "waiting through setup. Leave blank to launch as before.",
                id="seed-hint",
            )
            yield from self._widgets
            yield FocusGroup(
                [("launch", "Launch"), ("skip", "Skip")], id="seed-buttons")

    def on_mount(self) -> None:
        title = f"New worktree — {self._target}" if self._target else "New worktree"
        self.query_one("#seed-frame", Vertical).border_title = title
        self._rec["primary"].focus()

    def _advance_focus(self, widget) -> None:
        """Enter from the textarea (``_AutoExpandTextArea.on_key``'s
        accept-and-advance mechanic) -- there is only ever one field here, so
        this always advances straight to the button row (highlighting
        Launch), mirroring ``PivotFormScreen``'s single-question path."""
        try:
            group = self.query_one("#seed-buttons", FocusGroup)
            group._idx = 0  # highlight Launch
            group.focus()
        except Exception:
            pass

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        self.dismiss(self._collect() if event.value == "launch" else "")

    def action_skip(self) -> None:
        self.dismiss("")

    def _collect(self) -> str:
        return str(self._rec["primary"].text).strip()
