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
from textual.widget import Widget
from textual.widgets import (
    Input,
    RadioButton,
    Static,
    TabbedContent,
    TabPane,
)

from .steering import (
    ResetConfirmScreen,
    SteerButtonRow,
    _AutoExpandTextArea,
    _OTHER_LABEL,
    _OTHER_SENTINEL,
    _SteerRadioSet,
    _SteerSelectionList,
    _card_markdown,
    _steer_draft_path,
)


class PivotFormScreen(ModalScreen[dict]):
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
        name, ftype = f["name"], f["type"]
        options = list(f.get("options", []))
        allow_other = bool(f.get("allow_other"))
        rec = {
            "name": name,
            "type": ftype,
            "options": options,
            "allow_other": allow_other,
            "show_when": f.get("show_when"),
            "visible": True,
            "primary": None,
            "other": None,
        }
        yield Static(self._q_label(f), classes="steer-qlabel")
        if ftype == "choice":
            btns = [RadioButton(o, value=(j == 0)) for j, o in enumerate(options)]
            if allow_other:
                btns.append(RadioButton(_OTHER_LABEL))
            rs = _SteerRadioSet(*btns, id=f"q-{i}")
            rec["primary"] = rs
            yield rs
            if allow_other:
                other = _AutoExpandTextArea(id=f"other-{i}")
                other.display = False
                rec["other"] = other
                yield other
        elif ftype == "multichoice":
            sels = [(o, o) for o in options]
            if allow_other:
                sels.append((_OTHER_LABEL, _OTHER_SENTINEL))
            sl = _SteerSelectionList(*sels, id=f"q-{i}")
            rec["primary"] = sl
            yield sl
            if allow_other:
                other = _AutoExpandTextArea(id=f"other-{i}")
                other.display = False
                rec["other"] = other
                yield other
        else:  # text -> single-line Input; textarea -> free-form auto-expand box
            if ftype == "text":
                w: Widget = Input(id=f"q-{i}")
            else:
                w = _AutoExpandTextArea(id=f"q-{i}")
            rec["primary"] = w
            yield w
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

    # ---- dynamic "Other…" reveal --------------------------------------------
    def _rec_for(self, widget) -> dict | None:
        for rec in self._q:
            if rec["primary"] is widget:
                return rec
        return None

    def _question_index(self, widget) -> int:
        """Index of the question a widget belongs to (its primary OR its Other
        box), or -1."""
        for i, rec in enumerate(self._q):
            if rec["primary"] is widget or rec.get("other") is widget:
                return i
        return -1

    def on_radio_set_changed(self, event) -> None:
        # Reveal/hide the "Other…" free-text box for this question (display only;
        # focus is handled on Enter by _advance_after_choice so Space stays put).
        rec = self._rec_for(event.radio_set)
        if rec and rec.get("other"):
            other_idx = len(rec["options"])  # "Other…" follows the real options
            rec["other"].display = getattr(event, "index", -1) == other_idx
        self._sync_conditional_fields()

    def on_selection_list_selected_changed(self, event) -> None:
        rec = self._rec_for(event.selection_list)
        if not rec or not rec.get("other"):
            return
        selected = list(getattr(event.selection_list, "selected", []) or [])
        rec["other"].display = _OTHER_SENTINEL in selected

    def _condition_value(self, rec: dict) -> str:
        """Current scalar answer used by a dependent field predicate."""
        primary = rec.get("primary")
        if rec.get("type") == "choice":
            idx = getattr(primary, "pressed_index", -1)
            options = rec.get("options") or []
            if 0 <= idx < len(options):
                return str(options[idx])
        return ""

    def _condition_matches(self, rec: dict) -> bool:
        condition = rec.get("show_when")
        if not isinstance(condition, dict):
            return True
        controller = next(
            (item for item in self._q if item["name"] == condition.get("field")),
            None,
        )
        if controller is None:
            return True
        if not controller.get("visible", True):
            return False
        return self._condition_value(controller) == str(condition.get("equals", ""))

    def _visible_question_indexes(self) -> list[int]:
        return [i for i, rec in enumerate(self._q) if rec.get("visible", True)]

    def _sync_conditional_fields(self) -> None:
        """Show/hide dependent tabs after their controlling choice changes."""
        tabs = next(iter(self.query(TabbedContent)), None)
        for i, rec in enumerate(self._q):
            visible = self._condition_matches(rec)
            rec["visible"] = visible
            if tabs is not None:
                try:
                    (tabs.show_tab if visible else tabs.hide_tab)(f"tab-{i}")
                except Exception:
                    pass
        if tabs is not None:
            visible = self._visible_question_indexes()
            active = str(getattr(tabs, "active", ""))
            if visible and active not in {f"tab-{i}" for i in visible}:
                tabs.active = f"tab-{visible[0]}"

    # ---- keyboard flow: advance + tab cycling -------------------------------
    def _activate_tab(self, i: int) -> None:
        try:
            self.query_one("#steer-tabs", TabbedContent).active = f"tab-{i}"
        except Exception:
            pass

    def _advance_to_next_question(self, i: int) -> None:
        """Focus the next question's input (switching tabs), or the button row
        (Confirm) when there is none left."""
        following = [j for j in self._visible_question_indexes() if j > i]
        if following:
            next_i = following[0]
            self._activate_tab(next_i)
            try:
                self._q[next_i]["primary"].focus()
            except Exception:
                pass
        else:
            try:
                br = self.query_one(SteerButtonRow)
                br._idx = 0  # highlight Confirm
                br.focus()
            except Exception:
                pass

    def _advance_focus(self, widget) -> None:
        """Enter from a free-form / Other box: advance to the next question."""
        self._advance_to_next_question(self._question_index(widget))

    def _advance_after_choice(self, widget) -> None:
        """Enter from a choice/multichoice: if the toggle activated the "Other…"
        box, focus it (keep typing); otherwise advance to the next question."""
        rec = self._rec_for(widget)
        if rec and rec.get("other") is not None:
            if rec["type"] == "choice":
                other_active = getattr(widget, "pressed_index", -1) == len(rec["options"])
            else:
                other_active = _OTHER_SENTINEL in (getattr(widget, "selected", []) or [])
            if other_active:
                rec["other"].display = True
                try:
                    rec["other"].focus()
                except Exception:
                    pass
                return
        self._advance_to_next_question(self._question_index(widget))

    def _cycle_tab(self, direction: int) -> None:
        visible = self._visible_question_indexes()
        if len(visible) <= 1:
            return
        try:
            tabs = self.query_one("#steer-tabs", TabbedContent)
        except Exception:
            return
        try:
            cur = visible.index(int(str(tabs.active).split("-")[1]))
        except Exception:
            cur = 0
        i = visible[(cur + direction) % len(visible)]
        tabs.active = f"tab-{i}"
        try:
            self._q[i]["primary"].focus()
        except Exception:
            pass

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        self._cycle_tab(-1)

    # ---- collect / draft ----------------------------------------------------
    def _collect(self, *, include_hidden: bool = False) -> dict:
        values: dict = {}
        for rec in self._q:
            if not include_hidden and not rec.get("visible", True):
                continue
            name, ftype = rec["name"], rec["type"]
            options, allow_other = rec["options"], rec["allow_other"]
            prim, other = rec["primary"], rec["other"]
            if ftype == "text":
                values[name] = prim.value
            elif ftype == "textarea":
                values[name] = prim.text
            elif ftype == "choice":
                idx = getattr(prim, "pressed_index", -1)
                if allow_other and idx == len(options):
                    values[name] = other.text if other else ""
                elif 0 <= idx < len(options):
                    values[name] = options[idx]
                else:
                    values[name] = ""
            elif ftype == "multichoice":
                selected = list(getattr(prim, "selected", []) or [])
                members = [s for s in selected if s != _OTHER_SENTINEL]
                if (allow_other and _OTHER_SENTINEL in selected
                        and other and other.text.strip()):
                    members.append(other.text.strip())
                values[name] = members
        return values

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


