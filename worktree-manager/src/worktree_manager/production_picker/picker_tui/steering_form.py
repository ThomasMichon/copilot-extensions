"""``PivotFormScreen`` -- mechanical extraction from ``steering.py``.

This module exists only to control ``steering.py`` module size. The
extracted class was moved verbatim with no behavior change.
"""

from __future__ import annotations

import json

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
    SteerButtonRow,
    _card_markdown,
    _steer_draft_path,
)


class PivotFormScreen(FieldQuestionsMixin, ModalScreen[dict]):
    """Docked card + tabbed elicitation modal (the DISPATCH-pivot 'Steer' surface).

    A Copilot-CLI-style layout: the card's prose fills the top (a scrollable
    ``#steer-card`` that takes all the height the docked input doesn't need), and
    a **docked** elicitation section sits at the bottom -- one **tab per question**
    (``TabbedContent``; a single question skips the tab bar), each a single-select
    (``RadioSet``), multi-select (``SelectionList``), or free-form
    auto-expanding text box (up to 10 lines). A choice/multichoice that declares
    ``allow_other`` gains an **"Other…"** entry that reveals a free-text box.
    Beneath sits a single-line Picker-style button row, with three genuinely
    distinct semantics (not three names for the same close-and-persist action):

    * **Confirm** -- submits the answer for real (``agent-dispatch steer
      submit``): the task's ``card_draft`` is superseded, ``awaiting_steer``
      clears, and the task resumes/re-queues.
    * **Save** -- leaves the task exactly as blocked as it was, but durably
      persists the answer as the task's ``card_draft`` on the coordinator
      itself (``agent-dispatch card draft save``) -- visible from any surface
      or machine, not just a local sidecar file -- so a later Confirm (from
      this or any other Picker) starts from it.
    * **Reset** -- after an are-you-sure, clears every field in place (never
      closes the dialog) and clears the coordinator's saved ``card_draft`` too
      (``agent-dispatch card draft clear``); the task stays blocked.

    Every path that *closes* this screen (Confirm, Save, Esc) saves the
    collected answer to the on-disk draft first, before anything else happens
    -- so if the actual coordinator call (which runs asynchronously, after
    this screen has already dismissed) fails for any reason, the operator's
    answer is never silently lost; it is exactly what reopening this card's
    next steer prompt restores, and a failed delivery surfaces a blocking
    :class:`SubmitErrorScreen` rather than an easily-missed status line.
    Confirm's local draft is only deleted once the caller confirms the
    submission actually succeeded (mirroring the coordinator's own
    ``card_draft`` being cleared server-side on a successful steer -- see
    ``TaskQueue.submit_steer``); Save's local draft is deliberately left in
    place too, since Save never submits a steer.

    Confirm/Save both dismiss with ``{"action": "confirm"|"save", "values":
    dict}`` (a multichoice value is a list); this screen never runs a command
    and never carries a verdict itself -- it only gathers the operator's
    answer and tells the caller which coordinator call to make.

    Esc behaves exactly like Save (nothing is lost); Ctrl+S saves explicitly."""

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
                 task_id: str = "", on_clear_draft=None) -> None:
        super().__init__()
        self._card = card or {}
        self._fields = fields or []
        self._submit_label = submit_label or "Steer"
        self._task_id = str(task_id or "")
        # Fire-and-forget background hook the caller wires (see
        # ``_open_pivot_form``) so Reset can clear the operator's draft on the
        # coordinator itself (``card_draft``, distinct from an actual steer) --
        # a durable, cross-surface scratchpad -- not just a local sidecar file
        # only this machine can see. Optional: a caller with no coordinator
        # draft support (or a plain read-only card) passes ``None`` and this
        # screen behaves exactly as before (Reset only clears local state).
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
                yield SteerButtonRow(
                    [("confirm", "Confirm"), ("save", "Save"), ("reset", "Reset")],
                    self._on_button, id="steer-buttons",
                )

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
        return ("Enter accept+next · Shift+Enter newline · Space toggle · "
                "Ctrl+←/→ tabs · Ctrl+S save · Esc save+close  ·  "
                "Confirm sends this response to the agent · "
                "Reset clears the fields (asks first)")

    # ---- lifecycle ----------------------------------------------------------
    def on_mount(self) -> None:
        self.query_one("#steer-frame", Vertical).border_title = f"Steer — {self._submit_label}"
        # Choice defaults: pre-select the first real option (RadioSet index 0).
        restored = self._load_draft()
        if restored:
            self._restore(restored)
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

    def _restore(self, values: dict) -> None:
        for rec in self._q:
            name, ftype = rec["name"], rec["type"]
            options, allow_other = rec["options"], rec["allow_other"]
            prim, other = rec["primary"], rec["other"]
            if name not in values:
                continue
            v = values[name]
            if ftype == "text":
                prim.value = str(v)
            elif ftype == "textarea":
                prim.text = str(v)
                prim.autosize()
            elif ftype == "choice":
                btns = list(prim.query(RadioButton))
                if str(v) in options:
                    target = options.index(str(v))
                elif allow_other and other is not None:
                    target = len(options)  # "Other…"
                    other.text = str(v)
                    other.display = True
                    other.autosize()
                else:
                    target = -1
                for j, b in enumerate(btns):
                    b.value = (j == target)
            elif ftype == "multichoice":
                members = v if isinstance(v, list) else [v]
                extras = []
                for m in members:
                    if str(m) in options:
                        try:
                            prim.select(prim.get_option_at_index(options.index(str(m))))
                        except Exception:
                            pass
                    else:
                        extras.append(str(m))
                if allow_other and other is not None and extras:
                    other.text = ", ".join(extras)
                    other.display = True
                    other.autosize()
                    try:
                        prim.select(prim.get_option_at_index(len(options)))
                    except Exception:
                        pass

    def _write_draft(self) -> None:
        path = _steer_draft_path(self._task_id)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({
                    "task_id": self._task_id,
                    "values": self._collect(include_hidden=True),
                }),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _load_draft(self) -> dict:
        path = _steer_draft_path(self._task_id)
        try:
            if path and path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("values"), dict):
                    return data["values"]
        except (OSError, ValueError):
            pass
        return {}

    def _delete_draft(self) -> None:
        path = _steer_draft_path(self._task_id)
        try:
            if path and path.exists():
                path.unlink()
        except OSError:
            pass

    # ---- actions ------------------------------------------------------------
    def _on_button(self, key: str) -> None:
        if key == "confirm":
            self._confirm()
        elif key == "save":
            self.action_save()
        else:
            self._on_reset_pressed()

    def _confirm(self) -> None:
        # Save first: the caller submits asynchronously *after* this screen has
        # already dismissed, so if that submission fails for any reason, the
        # collected answer must still be recoverable rather than lost the
        # moment this dialog closes. The caller deletes this draft itself once
        # it confirms the submission actually succeeded.
        self._write_draft()
        values = self._collect()
        self.dismiss({"action": "confirm", "values": values})

    def _on_reset_pressed(self) -> None:
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
        for rec in self._q:
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
        self._delete_draft()
        if self._on_clear_draft is not None:
            self._on_clear_draft()
        try:
            self.query_one("#steer-buttons", SteerButtonRow).focus()
        except Exception:
            pass

    def action_save(self) -> None:
        # Local draft first (unchanged safety net -- survives even if the
        # coordinator call below fails or agent-dispatch is unreachable), then
        # dismiss so the caller can push the same answer onto the task itself
        # (``card_draft``) as the durable, cross-surface copy. The task stays
        # blocked exactly as before -- Save never submits a steer.
        self._write_draft()
        self.dismiss({"action": "save", "values": self._collect()})

    def action_escape_close(self) -> None:
        # Preserve work: Esc behaves exactly like Save (draft persisted both
        # locally and, once dismissed, on the coordinator) then closes without
        # submitting.
        self._write_draft()
        self.dismiss({"action": "save", "values": self._collect()})


