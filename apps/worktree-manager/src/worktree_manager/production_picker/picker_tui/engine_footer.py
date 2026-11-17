"""Focus-specific keyboard hints and current read/action status."""
from __future__ import annotations

from rich.text import Text

from .engine_helpers import C_DIM, C_META


class PickerFooterMixin:
    def _focus_hint(self):
        """Explain the controls for the currently focused region."""
        zone = self.sel[0]
        if zone == "UPD":
            return ("Enter: apply staged update + restart the picker"
                    " · ↑↓ move · Tab region")
        if zone == "MUP":
            return ("Enter: update the Worktree Manager + restart the picker"
                    " · ↑↓ move · Tab region")
        if zone == "V":
            return (f"◀▶ view / ⚙ Config (on {self.htabs[self.htab]})"
                    f" · Enter: focus body · Tab region · [ ] view")
        if zone == "CFG":
            return ("◀▶ back to pivots · Enter: focus sections"
                    " · ↑↓ move · Tab region")
        if zone == "M":
            if self._in_configuration():
                return "◀▶ section · Enter: focus content · Esc: return to previous view"
            m, e, _ = self.cur_machine()
            scope = "All machines" if self.is_all() else f"{m} {e}"
            return (f"◀▶ switch machine (on {scope}) · Enter: focus actions"
                    f" · Tab region · ^◀▶ machine")
        if zone == "BTN":
            btn = self.active_button()
            if btn == "N":
                tm, te = self.create_target()
                return (f"Enter: new worktree on {tm} {te}"
                        f" · Tab region · ^◀▶ machine")
            if btn == "K":
                return "Enter: open Cleanup dialog · ◀▶ Cleanup/Sync · Tab region"
            if btn == "SY":
                return "Enter: open Sync dialog · ◀▶ Cleanup/Sync · Tab region"
            if btn == "PA":
                n = self.pending_count()
                return (f"Enter: apply {n} profile change(s) · ◀▶ Apply/Reset"
                        f" · ↑ grid · Tab region")
            if btn == "PReset":
                return ("Enter: reset grid to applied · ◀▶ Apply/Reset"
                        " · ↑ grid · Tab region")
            if btn == "NC":
                reg = self._reg_pivot()
                label = reg.create_action.label if reg and reg.create_action else "entry"
                return (f"Enter: {label} · Tab region · ^◀▶ machine")
            if btn and btn.startswith("VA:"):
                actions = self._pivot_view_actions()
                index = int(btn.split(":")[1])
                return f"Enter: {actions[index].label} · Tab region" if index < len(actions) else ""
            return ""
        if zone == "T":
            reg = self._reg_pivot()
            label = reg.label if reg is not None else "entry"
            return (f"Enter: actions for {label} row · ↑↓ move"
                    " · Tab region · r reload")
        if zone == "L":
            rec = self._selected_record()
            wid = rec.get("id4") if rec else "?"
            nsel = len(self.wt_sel)
            if nsel:
                return (f"Space: select/deselect · Enter: actions for "
                        f"{nsel} selected · / search · s sort · g group · f status"
                        f" · Tab region · ^◀▶ machine")
            return (f"Space: select · Enter: sub-menu for worktree {wid}"
                    f" · / search · s sort · g group · f status · Tab region")
        if zone == "C":
            rec = self._selected_record()
            wid = rec.get("id4") if rec else "?"
            nsel = self.maint_sel.count(self._maint_ids())
            tgt = f"{nsel} selected" if nsel else f"row {wid}"
            return (f"Space: select {wid} · Enter: actions for {tgt}"
                    f" · Tab region · ^◀▶ machine")
        if zone == "SA":
            ids = self._maint_ids()
            all_on = self.maint_sel.all_selected(ids)
            verb = "clear all" if all_on else "select all"
            return f"Space / Enter: {verb} ({len(ids)}) · ↓ rows · Tab region"
        if zone == "GH":
            groups = self.maint_groups()
            gi = self.sel[1]
            st = groups[gi][0] if 0 <= gi < len(groups) else "?"
            return f"Space / Enter: select all {st} · ↓ rows · Tab region"
        if zone == "PR":
            ti = self.sel[1]
            if not self.host_cols:
                return "No profile hosts configured · Tab region"
            host = "{1} {2}".format(*self.host_cols[self.pcol])
            tlabel = (self.targets[ti]["label"]
                      if 0 <= ti < len(self.targets) else "?")
            return (f"Space: toggle {host} → {tlabel} · ◀▶ host · ↑↓ target"
                    f" · Enter: to Apply · Tab region")
        return ""

    def footer(self, W):
        if self.progress:
            hints = ("Esc cancel" if not self.progress["done"]
                     else "Enter/Esc close")
            if self.progress.get("op") == "sync" and self.progress.get("armed", True):
                if any((item.get("result") or {}).get("cleanup_pending")
                       for item in self.progress.get("items", [])):
                    hints = "Cleanup unconfirmed; exact custody retained"
                elif not self.progress["done"]:
                    hints = "Sync running; Escape does not cancel"
        else:
            hints = self._focus_hint()
            if self._kind() == "worktrees" and self._orphans:
                hints = "o: Doctor · " + hints
        f = Text(" " + hints, style=C_META)
        if self._busy_label:
            dbg = f"· {self.spin()} {self._busy_label}… "
        else:
            from .registered_scopes import refresh_footer

            status = refresh_footer(self, self.debug)
            if status == "loading" and self._kind() == "registered":
                state, _rows, _error = self._task_state()
                status = f"{self._reg_pivot().label}: " + {
                    "loading": "reading declared scope(s)",
                    "error": "read failed or incomplete; see source errors",
                    "ready": "read settled",
                }.get(state, state)
            dbg = f"· {status} "
        right = Text(dbg, style=C_DIM)
        if self.sel[0] == "T" and f.cell_len + right.cell_len > W:
            right.truncate(max(1, W * 2 // 3), overflow="ellipsis")
            f.truncate(max(0, W - right.cell_len), overflow="ellipsis")
        f.append(" " * max(0, W - f.cell_len - right.cell_len))
        f.append_text(right)
        f.truncate(W)
        return f
