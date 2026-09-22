#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

from . import derive
from .engine_dialogs import QuitConfirmScreen
from .engine_helpers import C_DIM
from .listview import capture_row_refs, remap_row_refs, render_command_bar
from .styles import canonical_key

class PickerScreenInputMixin:
    def _dispatch_key(self, key, character=None):
        # NOTE: this MUST NOT be named ``handle_key`` -- Textual's
        # ``Widget.handle_key`` is an ``async`` coroutine its ``_on_key``
        # awaits to run the BINDINGS system. Shadowing it with this synchronous
        # dispatcher makes Textual ``await`` a ``None`` return and crash
        # ("object NoneType can't be used in 'await' expression") whenever a
        # global BINDING key (Ctrl+←/→, F3/F4/F5) bubbles to the framework.
        # Keeping a distinct name lets Textual's native binding dispatch run.
        # Fold framework key-name aliases to canonical tokens once, up front, so
        # every downstream match is declarative and alias-free (#88 F2).
        # ``character`` (event.character) is the printable text a NAMED key
        # token (e.g. "slash") represents, for the "/" bar composer (#2228 P4).
        key = canonical_key(key)
        if self.cmd_mode:
            result = self._wt_remap(
                lambda: self.list_view.handle_compose_key(key, character))
            if result in ("commit", "cancel"):
                self.cmd_mode = False
            return
        # Remember the grid row before any navigation, so Tab out/in restores it.
        if self.sel and self.sel[0] == "PR":
            self.last_pr = self.sel[1]
        # Same for the Worktrees list: remember focus so Tab out/in restores it
        # (#2258 P3-6).
        if self.sel and self.sel[0] == "L":
            self.last_l = self.sel[1]
        # (Every modal overlay is a native ModalScreen now (#88 F4): while one is
        # up it sits above this widget on Textual's screen stack and owns the
        # keyboard, so _dispatch_key only ever runs for the top-level views --
        # there is no manual overlay to route to first.)

        # Global pivot/machine shortcuts. The Ctrl+Shift+←/→ (pivot) and Ctrl+←/→
        # (machine) combos are owned by Textual BINDINGS now (#88 F3) -- on_key
        # passes them to the framework, so they never reach here in the real
        # path; the action_* methods do the work. Only the printable [ ] pivot
        # shortcut stays in the manual dispatcher (on_key remaps its character,
        # and a bare letter is an awkward binding).
        if key == "[":
            return self._switch_pivot(-1)
        if key == "]":
            return self._switch_pivot(1)
        # "/" command bar + "s" sort-cycle (#2228 Phase 4) -- Worktrees only
        # for now; a registered pivot's own wiring is a later slice.
        if key == "/" and self._kind() == "worktrees":
            self.cmd_mode = True
            return
        if key == "s" and self._kind() == "worktrees":
            self._wt_remap(lambda: self.list_view.cycle_sort(derive.WT_SORT_KEYS))
            return

        zone = self.sel[0]

        # Tab / Shift+Tab jump between major regions (View / Machine / Buttons /
        # the table or grid).
        if key in ("tab", "shift+tab"):
            heads = self.region_heads()
            cur = self.region_head(zone)
            i = heads.index(cur) if cur in heads else 0
            d = 1 if key == "tab" else -1
            self.sel = heads[(i + d) % len(heads)]
            return

        # Bare ←/→ move to the next item *within* the focused region.
        if key in ("left", "right"):
            d = 1 if key == "right" else -1
            if zone in ("V", "CFG"):
                self._nav_view_row(d)      # traverse pivots + ⚙ Configuration
            elif zone == "M":
                self._rotate_machine(d)
            elif zone == "BTN":
                bset = self.button_set()
                if bset:
                    self.btn_idx = (self.btn_idx + d) % len(bset)
            elif zone == "PR":
                if self.host_cols:
                    self.pcol = (self.pcol + d) % len(self.host_cols)
            # L / C rows: bare ←/→ is a no-op (machine = Ctrl+←/→)
            return

        stops = self.stops()
        # ⚙ Configuration rides the View pivot row: for vertical up/down treat it
        # as the ("V",0) row stop, so ↑/↓ leave it for the row's neighbours.
        ref = ("V", 0) if self.sel[0] == "CFG" else self.sel
        if ref not in stops:
            self.sel = self.default_sel()
            ref = self.sel
        idx = stops.index(ref)
        zone = self.sel[0]

        if key in ("shift+up", "shift+down"):
            self._wt_shift_move(stops, idx, key == "shift+down")
        elif key in ("ctrl+up", "ctrl+down"):
            self._wt_ctrl_move(stops, idx, key == "ctrl+down")
        elif key == "down":
            self.sel = stops[min(idx + 1, len(stops) - 1)]
            self._wt_track_focus()
        elif key == "up":
            self.sel = stops[max(idx - 1, 0)]
            self._wt_track_focus()
        elif key in ("pagedown", "pageup"):
            self._page(stops, idx, forward=(key == "pagedown"))
            self._wt_track_focus()
        elif key == "enter":
            self._activate()
        elif key in ("space", "ctrl+space"):
            # Ctrl+Space toggles too (operator request): Textual delivers it as
            # "ctrl+at" (Ctrl+Space == NUL), canonicalized to "ctrl+space" up
            # front. It behaves identically to Space so the toggle works even
            # while the operator is holding Ctrl to move focus (#2258 follow-up).
            if zone == "L":
                self._toggle_wt(self.sel[1])
            elif zone == "C":
                self._toggle_maint(self.sel[1])
            elif zone == "SA":
                self._toggle_maint_all()
            elif zone == "GH":
                self._toggle_group(self.sel[1])
            elif zone == "PR":
                self._toggle_cell()
            elif zone == "T":
                self._open_task_menu()
            elif zone == "BTN":
                self._activate()
        elif key == "r":
            # Real reload: rebuild the data source (live mode re-fetches every
            # machine on its loader threads; fixture mode re-reads src.load()).
            self.setup()
            self.sel = self.default_sel()
            self.debug = "refreshed · reloaded worktrees"
        elif key in ("q", "escape"):
            # Esc: filter clear, then selection collapse (#2258 P3-5), then
            # quit-confirm (#1429) -- only when all three are already clear.
            if key == "escape" and self.list_view.query:
                self._wt_remap(self.list_view.clear)
                return
            if key == "escape" and self._wt_collapse_selection():
                return
            self._open_quit_confirm()
    def _wt_capture_row_refs(self):
        """Snapshot focus/anchor/last_l by stable key (listview.py)."""
        return capture_row_refs(self._l_ids(), self.sel, self.wt_anchor, self.last_l)
    def _wt_remap(self, fn):
        """Run ``fn`` (a Worktrees list mutation) with focus/anchor/last_l
        remapped across it by stable row key (#2228 Phase 4)."""
        refs = self._wt_capture_row_refs()
        result = fn()
        self._wt_restore_row_refs(refs)
        return result
    def _wt_restore_row_refs(self, refs):
        """Remap a _wt_capture_row_refs() snapshot onto the new list."""
        ids = self._l_ids()
        last_l, focus, anchor = remap_row_refs(refs, ids)
        if last_l is not None:
            self.last_l = last_l
        if refs["focus_idx"] is not None:
            if focus is not None:
                self.sel, self.last_l = ("L", focus), focus
            else:
                self.sel = self.default_sel()
        elif self.sel[0] == "L" and self.sel not in self.stops():
            self.sel = self.default_sel()
        self.wt_anchor = anchor
    def _cmd_bar_row(self, width):
        """The "/" command-bar chrome row (listview.py)."""
        return render_command_bar(self.list_view, self.cmd_mode, width, C_DIM,
                                   derive.WT_SORT_KEYS)
    def _open_quit_confirm(self):
        """Esc/q on a main pivot view asks before quitting (#1429).

        Migrated to a native Textual ``ModalScreen`` (#88 F4): the first overlay
        moved off the manual render/dispatch. Textual owns the screen stack,
        focus, and key routing; the dialog dismisses ``True`` to quit / ``False``
        to stay. Only reached when no manual overlay is open (those still consume
        Esc first). Defaults focus to *Stay* so a reflexive Enter never quits.
        """
        def _after(quit_it):
            if quit_it:
                self.app.exit()
        self.app.push_screen(QuitConfirmScreen(), _after)
    def _rotate_machine(self, d):
        was_in_table = self.sel[0] == "L"
        self.machine_idx = (self.machine_idx + d) % len(self.machines)
        if self.sel not in self.stops():
            self.sel = self.default_sel()
        # A machine switch lands on a different list, so drop the whole
        # selection state (operator request) -- keeping ids from the old tab
        # would leak invisible selections. If focus was in the table, reset to a
        # clean single-row selection on the top row (single-select tracks focus);
        # otherwise just clear.
        self.wt_sel.clear()
        self.wt_anchor = None
        if was_in_table and self._kind() == "worktrees" and self._wt_visible_records():
            self.sel = ("L", 0)
            self._wt_track_focus()   # wt_sel = {top row}, anchor = 0
    def _switch_pivot(self, d):
        was_v = self.sel[0] == "V"
        # Cycle only pivots on the left rail; Configuration-hosted pivots (e.g.
        # Profiles) are reached via the ⚙ menu, not this cycle (#1426).
        left = self._left_pivots()
        if not left:
            return
        if self.htab in left:
            cur = left.index(self.htab)
            self.htab = left[(cur + d) % len(left)]
        else:
            # Currently on a Configuration-hosted pivot: step onto the left rail.
            self.htab = left[0] if d > 0 else left[-1]
        self.btn_idx = 0
        self.top = 0
        # Stay in the View nav if that's where focus was; otherwise land on the
        # new pivot's default body stop.
        self.sel = ("V", 0) if was_v else self.default_sel()
    def _nav_view_row(self, d):
        """Move focus across the top tab-row -- the left-rail pivots plus the
        ⚙ Configuration entry -- with bare ◀▶. Landing on a pivot switches the
        view (as a tab implies); landing on Configuration just focuses it (Enter
        opens its menu). Configuration is thus part of the same row as the
        pivots, not a separate stop (#1426)."""
        left = self._left_pivots()
        if not left:
            return
        row: list = list(left)
        if self._config_pivots():
            row.append("cfg")
        if self.sel[0] == "CFG":
            cur = len(row) - 1
        elif self.htab in left:
            cur = left.index(self.htab)
        else:
            cur = 0
        target = row[(cur + d) % len(row)]
        if target == "cfg":
            self.sel = ("CFG", 0)
        else:
            self.htab = target
            self.btn_idx = 0
            self.top = 0
            self.sel = ("V", 0)
    def _toggle_cell(self):
        self.profiles_view.toggle_cell()
    def _page(self, stops, idx, forward):
        anchors = [a for a in self.anchors() if a in stops]
        positions = sorted(stops.index(a) for a in anchors)
        if forward:
            nxt = next((p for p in positions if p > idx), positions[-1] if positions else idx)
        else:
            nxt = next((p for p in reversed(positions) if p < idx),
                       positions[0] if positions else idx)
        self.sel = stops[nxt]
    def _key_progress(self, key):
        p = self.progress
        # D4 progress-reporting action: Esc/q cancels a running action (kills the
        # child via the recorded cancel callback); Enter/Esc closes a finished
        # one. Its own reader thread invalidated the pivot cache on completion.
        if p.get("kind") == "action-stream":
            if key in ("escape", "q") or (p["done"] and key in ("enter", "space")):
                if not p["done"]:
                    cancel = p.get("cancel")
                    if callable(cancel):
                        cancel()
                verb = p["verb"]
                if p["done"] and not p.get("error"):
                    state, tail = "done", p.get("msg") or ""
                elif p.get("error"):
                    state, tail = "failed", p.get("error") or ""
                else:
                    state, tail = "cancelled", ""
                self.progress = None
                if self.sel not in self.stops():
                    self.sel = self.default_sel()
                self.debug = f"{verb} {state}" + (f" · {tail[:80]}" if tail else "")
            return
        # Unarmed: the extra confirm gate (beyond-clean cleanup). Enter proceeds,
        # Esc cancels without touching anything.
        if not p.get("armed", True):
            if key in ("enter", "space"):
                p["armed"] = True
                self._start_progress()
            elif key in ("escape", "q"):
                self.progress = None
                self.executor = None
                self.debug = f"{p['verb'].lower()} cancelled · 0 worktrees"
            return
        if key in ("escape", "q") or (p["done"] and key in ("enter", "space")):
            verb = p["verb"].lower()
            n = len(p["items"])
            failed = sum(1 for it in p["items"] if it["state"] == "failed")
            state = "complete" if p["done"] else "cancelled"
            sim = " (sim)" if self.mock_mode else ""
            # Profiles Apply: bank the succeeded host columns before clearing.
            if p.get("op") == "profiles":
                self.profiles_view.commit_applied()
                self.progress = None
                self.executor = None
                tail = f" · {failed} failed" if failed else ""
                self.debug = (f"{verb} {state} · {n} host column(s){tail}"
                              " · restart the terminal app to see changes")
                return
            was_real = self.executor is not None and p["done"]
            self.progress = None
            self.executor = None
            if was_real:
                # A real Cleanup/Sync changed worktree state -- reload the
                # machines it touched so the list re-renders without a Picker
                # restart (#1421, live re-render half).
                self._refresh_after_maint(p)
            tail = f" · {failed} failed" if failed else ""
            self.debug = f"{verb} {state} · {n} worktrees{tail}{sim}"
    def _refresh_after_maint(self, p):
        """Reload the machines a real Cleanup/Sync just touched.

        Every touched source re-fetches on a thread (local included) and streams
        back in via the render tick -- the list stays honest after maintenance
        without a full Picker relaunch, and the refresh never blocks the UI
        (#1421).
        """
        targets = {(r.get("machine"), r.get("env"))
                   for r in p.get("recs", []) if r.get("machine")}
        if self.live and self.loader is not None:
            for m, e in targets:
                self.loader.reload(m, e)
            self.data = self.loader.records()
            # The reloads are asynchronous, so the touched machine's fresh
            # records haven't arrived yet -- defer the selection reconcile until
            # they settle (processed in _tick) so we don't drop survivors that
            # are still streaming back in (#2258 P3-7).
            self._wt_reconcile_after = targets or None
        elif not self.live:
            self.data = self.src.load()
            # Fixture reload is synchronous: reconcile now. Survivors stay
            # selected, cleaned rows drop out, and focus stays at the equivalent
            # list index (clamped) so it never lands on a phantom stop.
            self._reconcile_wt_sel()
            self._rehome_l_focus()
        # Reconcile the local machine's bound/mux liveness cache as part of the
        # automatic post-action refresh (dotfiles: "Reclaim ran but the row
        # stayed ACTIVE"). The reload above only READS the cached bound_live/
        # mux_live hints, so an action that killed a process or mux
        # (Reclaim/Stop/Repair) would re-render from the pre-action cache until
        # the periodic reconcile or the freshness TTL. Kicking the authoritative
        # tri-state sweep here makes every completed action re-render the TRUE
        # liveness. Local-only (it resolves local processes); best-effort,
        # off-thread, reloads only on a real change (an already-correct cache --
        # e.g. one the executor self-stamped -- is a silent no-op).
        local = getattr(self.src, "LOCAL", None)
        if (not self.live) or (local is not None and local in targets):
            blr_fn = getattr(self.src, "reconcile_bound_live", None)
            if callable(blr_fn):
                self._start_bound_live_reconcile(blr_fn)
