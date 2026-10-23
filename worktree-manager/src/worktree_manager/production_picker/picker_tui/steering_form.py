"""Contributed card forms with local recovery and acknowledged shared Reset."""

from __future__ import annotations

from worktree_manager.form_draft_storage import read_drafts, write_draft

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    RadioButton,
    Static,
    TabbedContent,
    TabPane,
)

from .field_questions import FieldQuestionsMixin
from .field_widgets import compose_field
from .steering import (
    ResetConfirmScreen,
    SubmitErrorScreen,
    SteerButtonRow,
    _card_markdown,
    _steer_draft_path,
)


class PivotFormScreen(FieldQuestionsMixin, ModalScreen[dict]):
    """Docked card and tabbed input; the caller owns every provider command.

    Confirm, Save and Escape atomically preserve a local response before
    dismissal. A failed local write keeps the form open. Production callers
    bind recovery to provider, installation/queue, task, action/schema, card
    and observed shared-draft revision; unbound legacy files are not adopted.
    Shared-operation copies are immutable per write and unconfirmed before
    invocation. Restore local explicitly copies a matching preserved response;
    cleanup failure never makes an obsolete copy automatically restorable.
    Save/Escape return all answers (including hidden partial work), whereas
    Confirm returns only currently applicable fields.

    Reset asks first and stays open. With shared draft support it freezes input
    until the caller acknowledges Clear; refusal preserves fields and recovery.
    Without shared support Reset clears only the local form.
    """

    CSS = """
    PivotFormScreen { align: center middle; background: $background 55%; }
    PivotFormScreen > #steer-frame {
        width: 90%; height: 90%;
        border: round #ffaf00; background: $surface; padding: 0 1;
    }
    PivotFormScreen #steer-card { height: 1fr; }
    PivotFormScreen Markdown { background: $surface; padding: 0 1; }
    PivotFormScreen MarkdownH1 {
        content-align: left middle; color: #ffaf00; background: $surface;
    }
    PivotFormScreen MarkdownH2, PivotFormScreen MarkdownH3 { color: #4aa3ff; }
    PivotFormScreen MarkdownBlock > .strong { color: #ffaf00; text-style: bold; }
    PivotFormScreen MarkdownBlock > .em { color: #a3a3a3; text-style: italic; }
    PivotFormScreen MarkdownBlockQuote {
        background: #17212b; border-left: outer #4aa3ff;
    }
    PivotFormScreen #steer-dock { height: auto; max-height: 65%; padding: 0; }
    PivotFormScreen .steer-qlabel { color: #ffaf00; height: auto; padding: 1 0 0 0; }
    PivotFormScreen .steer-note { color: grey; height: auto; padding: 1 0 0 0; }
    PivotFormScreen TextArea { border: round grey; background: $surface; }
    PivotFormScreen Input { border: round grey; background: $surface; }
    PivotFormScreen RadioSet, PivotFormScreen SelectionList {
        border: none; background: $surface; height: auto;
    }
    PivotFormScreen #steer-foot { color: grey; height: auto; padding: 1 0 0 0; }
    PivotFormScreen #steer-buttons { height: 1; padding: 0; }
    """
    BINDINGS = [
        Binding("escape", "escape_close", show=False),
        Binding("ctrl+s", "save", show=False),
        Binding("ctrl+right", "next_tab", show=False),
        Binding("ctrl+left", "prev_tab", show=False),
    ]

    def __init__(self, card: dict, fields: list[dict], submit_label: str,
                 task_id: str = "", on_clear_draft=None, *,
                 draft_binding: dict | None = None, draft_values: dict | None = None) -> None:
        super().__init__()
        self._card = card or {}
        self._fields = fields or []
        self._submit_label = submit_label or "Steer"
        self._task_id = str(task_id or "")
        self._draft_binding = draft_binding
        self._draft_values = draft_values or {}
        self._draft_pending = False
        self._draft_file = None
        self._draft_files = []
        self._automatic_draft_files = set()
        self._local_recoveries = []
        # The caller must finish a pending Reset with draft_reset_complete.
        self._on_clear_draft = on_clear_draft
        # Per-question runtime refs, filled during compose:
        #   {name, type, options, allow_other, primary, other}
        self._q: list[dict] = []

    # ---- compose ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        # Deferred (picker-startup-latency follow-up): see steering.py's
        # PivotCardScreen.compose for why this import isn't at module level.
        from textual.widgets import Markdown

        with Vertical(id="steer-frame"):
            with VerticalScroll(id="steer-card"):
                yield Markdown(_card_markdown(self._card), id="steer-card-body")
            with Vertical(id="steer-dock"):
                yield from self._compose_questions()
                yield Static(self._foot(), id="steer-foot")
                buttons = [("confirm", "Confirm"), ("save", "Save"), ("reset", "Reset")]
                if self._draft_binding is not None:
                    buttons.append(("restore", "Restore local"))
                yield SteerButtonRow(buttons, self._on_button, id="steer-buttons")

    def _compose_questions(self):
        if not self._fields:
            yield Static("(this card requests no input — Confirm to acknowledge)",
                         classes="steer-note")
            return
        if len(self._fields) == 1:
            yield from self._compose_one(self._fields[0], 0)
            return
        with TabbedContent(id="steer-tabs"):
            for i, f in enumerate(self._fields):
                with TabPane(self._field_label(f), id=f"tab-{i}"):
                    yield from self._compose_one(f, i)

    def _compose_one(self, f: dict, i: int):
        yield Static(self._q_label(f), classes="steer-qlabel")
        widgets, rec = compose_field(f, i)
        rec["show_when"] = f.get("show_when")
        rec["visible"] = True
        yield from widgets
        self._q.append(rec)

    # ---- rendering helpers --------------------------------------------------
    @staticmethod
    def _field_label(f: dict) -> str:
        return str(f["name"]).replace("_", " ").capitalize()

    @staticmethod
    def _q_label(f: dict) -> str:
        hints = {"textarea": "free text", "text": "free text",
                 "choice": "choose one", "multichoice": "choose any"}
        hint = hints.get(f["type"], "")
        if f.get("allow_other"):
            hint += " · Other… for free text"
        label = PivotFormScreen._field_label(f)
        return label + (f"  ({hint})" if hint else "") + ":"

    def _foot(self) -> str:
        recovery = (
            f" · {len(self._local_recoveries)} unconfirmed local response(s); "
            "Restore local copies the latest without sending"
            if self._local_recoveries else ""
        )
        return ("Enter accept+next · Shift+Enter newline · Space toggle · "
                "Ctrl+←/→ tabs · Ctrl+S save · Esc save+close  ·  "
                "Confirm sends this response to the agent · "
                "Reset clears the fields (asks first)" + recovery)

    # ---- lifecycle ----------------------------------------------------------
    def on_mount(self) -> None:
        self.query_one("#steer-frame", Vertical).border_title = f"Steer — {self._submit_label}"
        # Choice defaults: pre-select the first real option (RadioSet index 0).
        restored = self._load_draft()
        if restored:
            self._restore(restored)
        self.query_one("#steer-foot", Static).update(self._foot())
        self._sync_conditional_fields()
        # Focus the first question's input (or the card scroll when there is none).
        visible = self._visible_question_indexes()
        target = (
            self._q[visible[0]]["primary"]
            if visible
            else self.query_one("#steer-card", VerticalScroll)
        )
        try:
            target.focus()
        except Exception:
            pass

    # ---- dynamic "Other…" reveal, conditional sync, advance, collect --------
    # See FieldQuestionsMixin (field_questions.py) -- mixed in above.
    def _focus_final_control(self) -> None:
        try:
            br = self.query_one(SteerButtonRow)
            br._idx = 0  # highlight Confirm
            br.focus()
        except Exception:
            pass

    def _write_draft(self, *, pending: bool | None = None) -> bool:
        path = _steer_draft_path(self._task_id, self._draft_binding)
        if not path:
            return True
        try:
            if pending is None:
                pending = self._on_clear_draft is not None
            self._draft_file = write_draft(
                path, self._draft_binding, self._task_id, self._collect(include_hidden=True),
                pending=pending,
            )
            self._draft_files.append(self._draft_file)
            if not pending:
                self._automatic_draft_files.add(self._draft_file)
            return True
        except OSError as error:
            self.app.push_screen(SubmitErrorScreen(
                "Local recovery", f"Cannot preserve this response: {error}\nThe form remains open.", None,
            ))
            return False

    def _load_draft(self) -> dict:
        base = _steer_draft_path(self._task_id, self._draft_binding)
        if base:
            records = read_drafts(base, self._draft_binding)
            self._local_recoveries = [(path, data) for path, data in records if data.get("pending")]
            automatic = [(path, data) for path, data in records if not data.get("pending")]
            self._draft_files.extend(path for path, _ in automatic)
            self._automatic_draft_files.update(path for path, _ in automatic)
            if automatic:
                path, data = automatic[0]
                self._draft_file = path
                return data["values"]
        return self._draft_values

    def _draft_path(self):
        return self._draft_file or _steer_draft_path(self._task_id, self._draft_binding)

    def _delete_draft(self) -> bool:
        return self._retire_drafts(self._draft_files)

    def _retire_drafts(self, paths, *, before_submit=False) -> bool:
        failures = []
        removed = set()
        for path in set(paths):
            try:
                path.unlink(missing_ok=True)
                removed.add(path)
            except OSError as error:
                failures.append((path, error))
        self._draft_files = [path for path in self._draft_files if path not in removed]
        self._automatic_draft_files.difference_update(removed)
        if failures:
            detail = "\n".join(f"{path}: {error}" for path, error in failures)
            self.app.push_screen(SubmitErrorScreen(
                "Local recovery cleanup",
                ("Cannot prepare submission: old local drafts could not be retired. "
                 "The form remains open and no command was invoked.\n" if before_submit else
                 "Local cleanup failed. Unconfirmed copies are not automatically restored.\n") + detail,
                self._draft_path(),
            ))
        return not failures

    # ---- actions ------------------------------------------------------------
    def _on_button(self, key: str) -> None:
        if key == "confirm":
            self._confirm()
        elif key == "save":
            self.action_save()
        elif key == "restore":
            self.action_restore_local()
        else:
            self._on_reset_pressed()

    def action_restore_local(self) -> None:
        if self._draft_pending:
            return
        if not self._local_recoveries:
            self.query_one("#steer-foot", Static).update("No matching unconfirmed local response. " + self._foot())
            return
        path, data = self._local_recoveries[0]
        self._clear_fields()
        self._restore(data["values"])
        self._sync_conditional_fields()
        self._draft_file = path
        self._draft_files.append(path)

    def _confirm(self) -> None:
        if self._draft_pending:
            return
        # Save first: the caller submits asynchronously *after* this screen has
        # already dismissed, so if that submission fails for any reason, the
        # collected answer must still be recoverable rather than lost the
        # moment this dialog closes. The caller deletes this draft itself once
        # it confirms the submission actually succeeded.
        if not self._write_draft(pending=self._draft_binding is not None or self._on_clear_draft is not None):
            return
        if self._draft_binding is not None and not self._retire_drafts(
            self._automatic_draft_files, before_submit=True,
        ):
            return
        values = self._collect()
        self.dismiss({"action": "confirm", "values": values})

    def _on_reset_pressed(self) -> None:
        if self._draft_pending:
            return
        def _after(confirmed: bool | None) -> None:
            if confirmed:
                self._perform_reset()

        self.app.push_screen(ResetConfirmScreen(), _after)

    def _perform_reset(self) -> None:
        """Clear every field back to its blank/default state, in place --
        never closes this dialog. Also clears the on-disk draft and (when the
        caller wired coordinator draft support) the durable ``card_draft`` on
        the task itself, since a reset answer has nothing left worth
        recovering from either place."""
        if self._draft_pending:
            return
        if self._on_clear_draft is not None:
            if not self._write_draft():
                return
            self._draft_pending = True
            self.query_one("#steer-dock", Vertical).disabled = True
            self.query_one("#steer-foot", Static).update("Reset pending — waiting for the provider")
            self._on_clear_draft()
            return
        self.draft_reset_complete(True)

    def draft_reset_complete(self, ok: bool, revision: str | None = None) -> None:
        self._draft_pending = False
        self.query_one("#steer-dock", Vertical).disabled = False
        self.query_one("#steer-foot", Static).update(self._foot())
        if not ok:
            return
        if not self._delete_draft() and self._on_clear_draft is None:
            return
        if revision is not None and self._draft_binding is not None:
            self._draft_binding = {**self._draft_binding, "revision_from": revision}
        self._draft_values = {}
        self._local_recoveries = []
        self._clear_fields()
        self.query_one("#steer-foot", Static).update(self._foot())
        try:
            self.query_one("#steer-buttons", SteerButtonRow).focus()
        except Exception:
            pass

    def _clear_fields(self) -> None:
        for rec in self._q:
            rec.pop("restored_multichoice", None)
            ftype = rec["type"]
            prim, other = rec["primary"], rec["other"]
            if ftype == "text":
                prim.value = ""
            elif ftype == "textarea":
                prim.text = ""
                prim.autosize()
            elif ftype == "choice":
                for b in prim.query(RadioButton):
                    b.value = False
            elif ftype == "multichoice":
                prim.deselect_all()
            if other is not None:
                other.text = ""
                other.display = False
        self._sync_conditional_fields()

    def action_save(self) -> None:
        if self._draft_pending:
            return
        # Local draft first (unchanged safety net -- survives even if the
        # coordinator call below fails or agent-dispatch is unreachable), then
        # dismiss so the caller can push the same answer onto the task itself
        # (``card_draft``) as the durable, cross-surface copy. The task stays
        # blocked exactly as before -- Save never submits a steer.
        if not self._write_draft():
            return
        self.dismiss({"action": "save", "values": self._collect(include_hidden=True)})

    def action_escape_close(self) -> None:
        if self._draft_pending:
            return
        # Preserve work: Esc behaves exactly like Save (draft persisted both
        # locally and, once dismissed, on the coordinator) then closes without
        # submitting.
        if not self._write_draft():
            return
        self.dismiss({"action": "save", "values": self._collect(include_hidden=True)})
