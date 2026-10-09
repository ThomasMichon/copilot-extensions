"""``FieldQuestionsMixin`` -- shared multi-field navigation/collection logic.

Mechanical extraction from ``PivotFormScreen`` (the Steer surface), mirroring
``field_widgets.py``'s own precedent: a screen that renders several
``field_widgets.compose_field`` questions (optionally across tabs, optionally
with ``show_when`` conditional visibility) needs the SAME "Other…" reveal,
conditional-tab sync, Enter-to-advance, and value-collection mechanics
regardless of what its own button row or persistence semantics look like.
``PivotFormScreen`` (docked card + Confirm/Save/Reset + on-disk draft) and
``CreateActionScreen`` (Phase B's pivot-level "create" affordance --
Confirm/Cancel, no draft) both mix this in rather than duplicating it.

A caller must:

* populate ``self._q: list[dict]`` during ``compose`` (the same ``rec`` shape
  ``field_widgets.compose_field`` returns, plus its own ``show_when``/
  ``visible`` bookkeeping -- see ``PivotFormScreen._compose_one`` for the
  pattern);
* override :meth:`_focus_final_control` to focus whatever sits after the last
  question (a button row) -- the base implementation is a no-op.

Tab cycling/conditional-sync assume at most one ``TabbedContent`` on the
screen (true for every current caller), so they query it generically by
type rather than a caller-specific id.
"""

from __future__ import annotations

from textual.css.query import NoMatches
from textual.widgets import TabbedContent

from .field_widgets import _OTHER_SENTINEL


class FieldQuestionsMixin:
    """Mix into a ``ModalScreen`` that renders ``self._q`` via
    ``field_widgets.compose_field``. See module docstring."""

    _q: list[dict]

    def _focus_final_control(self) -> None:
        """Focus whatever follows the last question (typically a button
        row) once Enter advances past it. No-op by default."""

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
        if tabs is not None:
            try:
                ready = all(tabs.get_tab(f"tab-{i}").is_mounted for i in range(len(self._q)))
            except NoMatches:
                ready = False
            if not ready:
                # Screen mount and initial choice events can precede the
                # TabbedContent children's mount; retry after their refresh.
                self.call_after_refresh(self._sync_conditional_fields)
                return
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
            self.query_one(TabbedContent).active = f"tab-{i}"
        except Exception:
            pass

    def _advance_to_next_question(self, i: int) -> None:
        """Focus the next question's input (switching tabs), or the final
        control (a button row) when there is none left."""
        following = [j for j in self._visible_question_indexes() if j > i]
        if following:
            next_i = following[0]
            self._activate_tab(next_i)
            try:
                self._q[next_i]["primary"].focus()
            except Exception:
                pass
        else:
            self._focus_final_control()

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
            tabs = self.query_one(TabbedContent)
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

    # ---- collect --------------------------------------------------------
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
