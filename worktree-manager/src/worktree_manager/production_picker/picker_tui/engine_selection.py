#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

from rich.text import Text

from .engine_helpers import C_BTN, C_BTN_LAST, C_BTN_SEL, C_DIM, C_ENV, C_LABEL, C_META
from .engine_dialogs import MaintMenuScreen

class PickerScreenSelectionMixin:
    def maint_groups(self):
        return self.maintenance_view.maint_groups()
    def maint_records(self):
        return self.maintenance_view.maint_records()
    def _maint_ids(self):
        return self.maintenance_view._maint_ids()
    def _toggle_maint(self, i):
        self.maintenance_view._toggle_maint(i)
    def _toggle_maint_all(self):
        self.maintenance_view._toggle_maint_all()
    def _toggle_group(self, gi):
        self.maintenance_view._toggle_group(gi)
    def _open_maint_menu(self):
        """Open the Maintenance actions menu for the selected set; if nothing
        is selected, select the focused row and open it for that one (#1345).

        Migrated to a native Textual ``ModalScreen`` (#88 F4): ``push_screen``s a
        ``MaintMenuScreen`` with the available actions and returns the chosen
        action *index* via ``dismiss(int|None)``; ``_run_maint_action`` runs the
        selection (``None`` cancels)."""
        if not self.maint_sel:
            rec = self._selected_record()
            if not rec:
                return
            self.maint_sel.replace({rec["id4"]})
        ids = self.maint_sel.ids
        chosen = [r for r in self.maint_records() if r["id4"] in ids]
        acts = []
        if any(
            r.get("ff_eligible") and self._record_supports(r, "sync")
            for r in chosen
        ):
            acts.append("Sync")
        if any(
            self._cleanable(r) and self._record_supports(r, "cleanup")
            for r in chosen
        ):
            acts.append("Cleanup")
        if not acts:
            # Nothing actionable for this selection (no FF-eligible or cleanable
            # worktree) -- don't open an empty menu.
            self.debug = (f"no maintenance action for {len(chosen)} "
                          f"selected worktree(s)")
            return
        self._push_maint_menu(ids, acts, len(chosen))
    def _push_maint_menu(self, ids, acts, count):
        """Push the Maintenance actions ModalScreen for ``acts`` over the ``ids``
        set (``count`` worktrees), running the chosen action on dismiss. Shared
        by both openers (the maintenance selection and the Worktrees-list bulk
        action menu), which build ``ids``/``acts`` differently but dispatch
        identically (#88 F4)."""
        def _after(choice):
            if choice is None:
                return
            self._run_maint_action(acts[choice], ids)
        self.app.push_screen(MaintMenuScreen(acts, count), _after)
    def _run_maint_action(self, act, ids):
        """Run one Maintenance action against the ``ids`` set. Mirrors the former
        ``_key_maint_menu`` Enter branch exactly (#88 F4)."""
        capability = {
            "Sync": "sync",
            "Cleanup": "cleanup",
            "Finalize": "finalize",
            "Stop": "stop",
            "Reclaim": "reclaim",
        }.get(act)
        if capability:
            ids = {
                self._row_key(rec)
                for rec in self.list_records()
                if (
                    (self._row_key(rec) in ids or rec["id4"] in ids)
                    and self._record_supports(rec, capability)
                )
            }
        if act == "Sync":
            self._open_sync(ids=ids)
        elif act == "Cleanup":
            self._open_cleanup(ids=ids)
        elif act == "Finalize":
            self._start_finalize(
                [r for r in self.list_records() if self._row_key(r) in ids])
        elif act == "Stop":
            self._start_stop(
                [r for r in self.list_records() if self._row_key(r) in ids])
        elif act == "Reclaim":
            self._start_reclaim(
                [r for r in self.list_records() if self._row_key(r) in ids])
    def _l_ids(self):
        """Collision-safe selection key of each VISIBLE row, render order
        (nav/focus-tracking only; actions resolve against list_records())."""
        return [self._row_key(r) for r in self._wt_visible_records()]
    def _wt_focused_id(self):
        """The id4 of the currently focused Worktrees row, or None when focus is
        outside the list."""
        if self.sel[0] != "L":
            return None
        ids = self._l_ids()
        i = self.sel[1]
        return ids[i] if 0 <= i < len(ids) else None
    def _wt_multiselect_active(self):
        """True when the Worktrees list is in a discoverable *multi-select* state
        (#2258 follow-up): more than one row selected, or the (single) selection
        has diverged from the focused row (e.g. after Ctrl+Arrow moved focus off
        it). In the common single-select-tracks-focus case the selection is just
        the focused row, so the checkbox gutter stays hidden -- it is only shown
        once the operator is actually building/holding a set."""
        if not self.wt_sel:
            return False
        if len(self.wt_sel) > 1:
            return True
        return self._wt_focused_id() not in self.wt_sel
    def _l_head(self):
        """Worktrees list entry point for Tab, restoring the last-focused row
        (#2258 P3-6, mirrors the Profiles-grid cell memory #1288)."""
        n = len(self._wt_visible_records())
        if n == 0:
            return ("L", 0)
        return ("L", min(self.last_l, n - 1))
    def _reconcile_wt_sel(self):
        """Keep the Worktrees selection honest across a reload (#2258 P3-7).

        Intersect the selection with the ids still present and reset the range
        anchor (row indices may have shifted, so a stale index would extend the
        wrong range -- the next Shift gesture re-seeds it from the live focus).
        Guarded against the live empty-load race: while the reloaded records are
        still streaming in (``list_records`` momentarily empty) this is a no-op,
        so a transient empty frame never clobbers a built-up selection.
        """
        try:
            recs = self.list_records()
        except Exception:
            return
        if not recs:
            return
        present = {self._row_key(r) for r in recs}
        if self.wt_sel:
            self.wt_sel.replace(self.wt_sel.ids & present)
        self.wt_anchor = None
    def _rehome_l_focus(self):
        """Keep list focus at the equivalent index after a reload removed rows
        (#2258 P3-7): clamp into the current VISIBLE list, or fall back to a
        safe default when it emptied."""
        if self.sel[0] != "L":
            return
        n = len(self._wt_visible_records())
        if n == 0:
            self.sel = self.default_sel()
        elif self.sel[1] >= n:
            self.sel = ("L", n - 1)
    def _process_pending_wt_reconcile(self):
        """Live mode: defer the post-operation selection reconcile until the
        reloaded machine(s) actually settle (#2258 P3-7).

        ``_refresh_after_maint`` kicks off asynchronous per-machine reloads, so
        reconciling immediately would race the streaming load and could drop
        survivors that simply haven't re-arrived yet. Instead we hold the touched
        targets and reconcile from ``_tick`` once every one reports ``ready``
        (or failed -- a machine that can't reload should not wedge the pending
        reconcile forever)."""
        targets = self._wt_reconcile_after
        if not targets or self.loader is None:
            self._wt_reconcile_after = None
            return
        try:
            settled = all(self.loader.state(m, e) in ("ready", "failed", "error")
                          for m, e in targets)
        except Exception:
            settled = True
        if settled:
            self._wt_reconcile_after = None
            self._reconcile_wt_sel()
            self._rehome_l_focus()
    def _wt_track_focus(self):
        """Plain-arrow rule (#2258 P3-1): the selection follows focus.

        When focus lands on a Worktrees row, collapse the multi-select to just
        that row and re-seat the range anchor there (single-select tracks
        focus); when focus leaves the list, the selection follows it to nothing.
        """
        if self.sel[0] == "L":
            ids = self._l_ids()
            i = self.sel[1]
            if 0 <= i < len(ids):
                self.wt_sel.replace({ids[i]})
                self.wt_anchor = i
                return
        if self.wt_sel:
            self.wt_sel.clear()
        self.wt_anchor = None
    def _wt_shift_move(self, stops, idx, forward):
        """Shift+arrow (#2258 P3-2): range-extend from the anchor.

        On the Worktrees list, move focus one row (clamped inside the list) and
        set the selection to the contiguous range from the anchor (seeded on the
        first shift move) to the newly focused row. Off the list it degrades to
        a plain focus move.
        """
        if self.sel[0] != "L":
            self.sel = (stops[min(idx + 1, len(stops) - 1)] if forward
                        else stops[max(idx - 1, 0)])
            self._wt_track_focus()
            return
        ids = self._l_ids()
        if not ids:
            return
        if self.wt_anchor is None or not (0 <= self.wt_anchor < len(ids)):
            self.wt_anchor = self.sel[1]
        new_i = max(0, min(self.sel[1] + (1 if forward else -1), len(ids) - 1))
        self.sel = ("L", new_i)
        lo, hi = sorted((self.wt_anchor, new_i))
        self.wt_sel.replace(ids[lo:hi + 1])
    def _wt_ctrl_move(self, stops, idx, forward):
        """Ctrl+arrow (#2258 P3-4): move focus only.

        Move the focus cursor one Worktrees row (clamped inside the list)
        without touching the selection or the range anchor, so the operator can
        navigate a built-up set without disturbing it. Off the list it degrades
        to a plain focus move (selection untouched)."""
        if self.sel[0] != "L":
            self.sel = (stops[min(idx + 1, len(stops) - 1)] if forward
                        else stops[max(idx - 1, 0)])
            return
        ids = self._l_ids()
        if not ids:
            return
        new_i = max(0, min(self.sel[1] + (1 if forward else -1), len(ids) - 1))
        self.sel = ("L", new_i)
    def _wt_collapse_selection(self):
        """Esc-collapse (#2258 P3-5): reduce a multi-selection, don't quit.

        With more than one row selected, reduce to just the focused row (or to
        nothing when focus is outside the list) and return True so Esc does NOT
        also open the quit-confirm (#1429). Returns False when there is nothing
        to collapse, letting Esc fall through to the quit prompt.
        """
        if len(self.wt_sel) <= 1:
            return False
        if self.sel[0] == "L":
            ids = self._l_ids()
            i = self.sel[1]
            if 0 <= i < len(ids):
                self.wt_sel.replace({ids[i]})
                self.wt_anchor = i
                self.debug = "collapsed selection to focused row"
                return True
        self.wt_sel.clear()
        self.wt_anchor = None
        self.debug = "collapsed selection"
        return True
    def _toggle_wt(self, i):
        """Space on a Worktrees row toggles it in the list selection (#2258
        P3-3: additive, independent of the rest) and re-seats the range anchor
        there so a following Shift+arrow extends from this row. ``i`` is a
        VISIBLE-list index, resolved against ``_wt_visible_records()``."""
        recs = self._wt_visible_records()
        if 0 <= i < len(recs):
            rec = recs[i]
            key = self._row_key(rec)
            now_on = self.wt_sel.toggle(key)
            self.wt_anchor = i
            self.debug = (
                f"{'selected' if now_on else 'deselected'} {rec['id4']}"
                f" · {len(self.wt_sel)} selected"
            )
    def _open_wt_action_menu(self):
        """Enter on the Worktrees list with a multi-selection opens the bulk
        action menu (Sync / Cleanup) for the selected set -- the same
        ``maint_menu`` the eliminated Maintenance view used, so Sync/Cleanup run
        through the identical scoped executor (#2228 2b). A single focused row
        with no selection keeps opening its per-row sub-menu (which carries
        Open/Resume) -- see ``_activate``."""
        ids = self.wt_sel.ids
        chosen = [
            r for r in self.list_records()
            if self._row_key(r) in ids or r["id4"] in ids
        ]
        acts = []
        if any(
            r.get("ff_eligible") and self._record_supports(r, "sync")
            for r in chosen
        ):
            acts.append("Sync")
        if any(
            self._cleanable(r) and self._record_supports(r, "cleanup")
            for r in chosen
        ):
            acts.append("Cleanup")
        if any(
            r.get("cleanup_bucket") in ("conversation", "unused")
            and self._record_supports(r, "finalize")
            for r in chosen
        ):
            acts.append("Finalize")
        if any(
            r.get("mux_live") and self._record_supports(r, "stop")
            for r in chosen
        ):
            acts.append("Stop")
        if any(
            type(self)._reclaimable(r) and self._record_supports(r, "reclaim")
            for r in chosen
        ):
            # Bulk parity with the per-row menu: reap the un-muxed bound Copilots
            # across the selection -- the ones Stop cannot reach (#4058).
            acts.append("Reclaim")
        if not acts:
            self.debug = (f"no bulk action for {len(chosen)} "
                          f"selected worktree(s)")
            return
        self._push_maint_menu(ids, acts, len(chosen))
    def profiles_present(self):
        return self.profiles_view.profiles_present()
    def _column_sels(self, hi):
        return self.profiles_view._column_sels(hi)
    def _apply_profiles(self):
        return self.profiles_view.apply()
    def _btn_style(self, focus, is_active):
        """Focused active button glows; unfocused active button shows a subtle
        'last-focused' highlight; others are at rest."""
        if focus and is_active:
            return C_BTN_SEL
        if (not focus) and is_active:
            return C_BTN_LAST
        return C_BTN
    def two_button_row(self, l1, l2, focus, active_idx, suffix, width):
        t = Text("  ")
        t.append(f" {l1} ", style=self._btn_style(focus, active_idx == 0))
        t.append("   ")
        t.append(f" {l2} ", style=self._btn_style(focus, active_idx == 1))
        if suffix and t.cell_len + 4 + len(suffix) <= width:
            t.append("    " + suffix, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t
    def _action_row_caption(self, focus, active_idx, bset, tm, te, host_tag):
        """The dim caption trailing the Worktrees action buttons -- specific to
        the *focused* button (operator feedback on #1427): New shows where it
        creates; Clean/Sync show the live count of worktrees the op would touch;
        Toggle shows the reveal/hide count. Defaults to the New caption when the
        row isn't focused."""
        code = bset[active_idx] if (focus and 0 <= active_idx < len(bset)) else "N"
        ctx = Text()
        if code == "K":
            n = sum(1 for r in self.list_records() if self._cleanable(r))
            ctx.append("    cleans ", style=C_DIM)
            ctx.append(f"{n}", style=C_LABEL)
            ctx.append(f" merged / idle worktree{'' if n == 1 else 's'} in ",
                       style=C_DIM)
            ctx.append(self._scope_label(), style=C_META)
        elif code == "SY":
            n = sum(1 for r in self.list_records() if r.get("ff_eligible"))
            ctx.append("    fast-forwards ", style=C_DIM)
            ctx.append(f"{n}", style=C_LABEL)
            ctx.append(f" eligible worktree{'' if n == 1 else 's'} in ",
                       style=C_DIM)
            ctx.append(self._scope_label(), style=C_META)
        elif code == "TH":
            nh = self._hidden_count()
            ctx.append(f"    {'hides' if self.show_hidden else 'reveals'} ",
                       style=C_DIM)
            ctx.append(f"{nh}", style=C_LABEL)
            ctx.append(" bridge / system worktree(s)", style=C_DIM)
        else:                                   # New worktree (default)
            ctx.append("    creates on ", style=C_DIM)
            if tm is None:
                ctx.append("…", style=C_DIM)
            else:
                ctx.append(f"{tm} ", style=C_META)
                if te:
                    ctx.append(te, style=C_ENV.get(te, C_META))
            if host_tag:
                ctx.append(host_tag, style=C_DIM)
        return ctx
    def new_worktree_row(self, width, focus, active_idx):
        """The Worktrees action row. New worktree opens the options dialog
        (#1346); the bulk Clean / Sync buttons opened here replaced the standalone
        Maintenance view (#1427); a right-aligned Toggle-hidden chip joins when
        something is hidden (#1422). Chips render generically from ``button_set``
        so adding a button never needs a hardcoded index."""
        bset = self.button_set()
        tm, te = self.create_target()
        host_tag = "  (this host)" if (tm, te) == self._src_local() else ""
        labels = {"N": " + New worktree… ", "K": " ✕ Clean ", "SY": " ⟳ Sync "}

        t = Text("  ")
        first = True
        for i, code in enumerate(bset):
            if code == "TH":
                continue                       # right-aligned, handled below
            if not first:
                t.append("   ")
            first = False
            t.append(labels.get(code, f" {code} "),
                     style=self._btn_style(focus, active_idx == i))

        toggle = None
        if "TH" in bset:
            th_i = bset.index("TH")
            nh = self._hidden_count()
            tlabel = " Hide hidden " if self.show_hidden else f" Show hidden ({nh}) "
            toggle = Text(tlabel, style=self._btn_style(focus, active_idx == th_i))

        ctx = self._action_row_caption(focus, active_idx, bset, tm, te, host_tag)

        right_w = (toggle.cell_len + 2) if toggle is not None else 0
        if t.cell_len + ctx.cell_len + 3 + right_w <= width:
            t.append_text(ctx)
        if toggle is not None:
            t.append(" " * max(1, width - t.cell_len - right_w))
            t.append_text(toggle)
            t.append("  ")
        else:
            t.append(" " * max(0, width - t.cell_len))
        return t
    def button_row(self, label, suffix, selected, width):
        """A row that reads as a button: a filled chip + dim trailing context
        (the context is dropped when it doesn't fit)."""
        t = Text("  ")
        t.append(f" {label} ", style=C_BTN_SEL if selected else C_BTN)
        if suffix and t.cell_len + 3 + len(suffix) <= width:
            t.append("   " + suffix, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t
    def _checkbox(self, checked):
        return ("☑", "green") if checked else ("☐", "grey50")
