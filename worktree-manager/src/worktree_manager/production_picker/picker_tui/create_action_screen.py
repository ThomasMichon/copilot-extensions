"""``CreateActionScreen`` -- Phase B's pivot-level "create new entry" modal.

Collects a registered pivot's ``create_action.fields`` (text/textarea/choice/
multichoice, with ``show_when`` conditional visibility -- the exact
``pivot_create_action.CreateAction`` schema) and dismisses with the
submitted values, or ``None`` on Cancel/Escape. Mirrors ``SeedPromptScreen``'s
shape (a lean, purpose-built modal, not a reuse of ``PivotFormScreen`` --
there is no card, no draft, no Save/Reset, just Confirm/Cancel) while reusing
:class:`~.field_questions.FieldQuestionsMixin` for the multi-field tab/
conditional/advance/collect mechanics ``PivotFormScreen`` already relies on.

When the manifest's ``create_action.confirm`` is set, pressing Confirm first
swaps to an inline are-you-sure prompt (mirroring ``ResetConfirmScreen``'s
FocusGroup pattern) rather than pushing a second screen -- *Cancel* is the
initial choice so a reflexive Enter never submits by accident.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TabbedContent, TabPane

from .engine_focus import FocusGroup
from .field_questions import FieldQuestionsMixin
from .field_widgets import compose_field, field_label


class CreateActionScreen(FieldQuestionsMixin, ModalScreen[dict | None]):
    """Collects a pivot-level create action's fields. See module docstring."""

    CSS = """
    CreateActionScreen { align: center middle; background: $background 55%; }
    CreateActionScreen > #create-frame {
        width: 80%; height: auto; max-height: 85%;
        border: round #ffaf00; background: $surface; padding: 0 2;
    }
    CreateActionScreen .create-qlabel { color: #ffaf00; height: auto; padding: 1 0 0 0; }
    CreateActionScreen TextArea { border: round grey; background: $surface; }
    CreateActionScreen Input { border: round grey; background: $surface; }
    CreateActionScreen RadioSet, CreateActionScreen SelectionList {
        border: none; background: $surface; height: auto;
    }
    CreateActionScreen #create-foot { color: grey; height: auto; padding: 1 0; }
    CreateActionScreen FocusGroup { height: 1; margin: 0 0 1 0; padding: 0; }
    CreateActionScreen #create-confirm-prompt { height: auto; padding: 1 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("ctrl+right", "next_tab", show=False),
        Binding("ctrl+left", "prev_tab", show=False),
    ]

    def __init__(self, label: str, fields: list, *, confirm: bool = False) -> None:
        super().__init__()
        self._label = label
        self._fields = list(fields or [])
        self._needs_confirm = bool(confirm)
        self._q: list[dict] = []
        self._confirming = False

    # ---- compose --------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="create-frame"):
            yield from self._compose_questions()
            yield Static(self._foot(), id="create-foot")
            yield FocusGroup(
                [("confirm", "Create"), ("cancel", "Cancel")], id="create-buttons")

    def _compose_questions(self):
        if not self._fields:
            yield Static("(no fields declared)", classes="create-qlabel")
            return
        if len(self._fields) == 1:
            yield from self._compose_one(self._fields[0], 0)
            return
        with TabbedContent(id="create-tabs"):
            for i, f in enumerate(self._fields):
                with TabPane(field_label(f), id=f"tab-{i}"):
                    yield from self._compose_one(f, i)

    def _compose_one(self, f: dict, i: int):
        yield Static(self._q_label(f), classes="create-qlabel")
        widgets, rec = compose_field(f, i)
        rec["show_when"] = f.get("show_when")
        rec["visible"] = True
        yield from widgets
        self._q.append(rec)

    @staticmethod
    def _q_label(f: dict) -> str:
        hints = {"textarea": "free text", "text": "free text",
                 "choice": "choose one", "multichoice": "choose any"}
        hint = hints.get(f["type"], "")
        if f.get("allow_other"):
            hint += " · Other… for free text"
        label = field_label(f)
        return label + (f"  ({hint})" if hint else "") + ":"

    def _foot(self) -> str:
        return ("Enter accept+next · Shift+Enter newline · Space toggle · "
                "Ctrl+←/→ tabs · Esc cancel")

    # ---- lifecycle --------------------------------------------------------
    def on_mount(self) -> None:
        self.query_one("#create-frame", Vertical).border_title = self._label
        self._sync_conditional_fields()
        visible = self._visible_question_indexes()
        target = self._q[visible[0]]["primary"] if visible else None
        try:
            (target or self.query_one("#create-buttons", FocusGroup)).focus()
        except Exception:
            pass

    def _focus_final_control(self) -> None:
        try:
            group = self.query_one("#create-buttons", FocusGroup)
            group._idx = 0  # highlight Create
            group.focus()
        except Exception:
            pass

    # ---- actions ------------------------------------------------------------
    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        if event.value == "cancel":
            self.dismiss(None)
            return
        if event.group.id == "create-confirm-prompt-buttons":
            if event.value == "yes":
                self.dismiss(self._collect())
            else:
                self._cancel_confirm_prompt()
            return
        if self._needs_confirm and not self._confirming:
            self._show_confirm_prompt()
            return
        self.dismiss(self._collect())

    def _show_confirm_prompt(self) -> None:
        self._confirming = True
        frame = self.query_one("#create-frame", Vertical)
        for child in list(frame.children):
            child.display = False
        prompt = Static(
            f"Create this {self._label.lower()}?", id="create-confirm-prompt")
        buttons = FocusGroup(
            [("yes", "Create"), ("no", "Cancel")], initial=1,
            id="create-confirm-prompt-buttons")
        frame.mount(prompt)
        frame.mount(buttons)
        buttons.focus()

    def _cancel_confirm_prompt(self) -> None:
        self._confirming = False
        frame = self.query_one("#create-frame", Vertical)
        for widget_id in ("#create-confirm-prompt", "#create-confirm-prompt-buttons"):
            try:
                frame.query_one(widget_id).remove()
            except Exception:
                pass
        for child in list(frame.children):
            child.display = True
        self._focus_final_control()

    def action_cancel(self) -> None:
        if self._confirming:
            self._cancel_confirm_prompt()
            return
        self.dismiss(None)
