#!/usr/bin/env python3
"""Profiles picker view extracted from ``engine.py``."""
from __future__ import annotations

import threading

from rich.text import Text

from .. import profiles as profiles_mod
from .engine_dialogs import ProfConfirmScreen
from .engine_helpers import (
    C_BTN_SEL,
    C_DIM,
    C_DISABLED,
    C_ENV,
    C_HEADER,
    C_HINT,
    C_LABEL,
    C_MUTED,
    C_PULSE,
    C_SEL,
    C_TABOFF,
    _DEFAULT_HOST_COLS,
)

class ProfilesView:
    """Encapsulated Profiles-configurator sub-view (#88 F5, slices 1-3).

    A cohesive sub-view carved out of ``PickerScreen.build_body``, per the
    incremental componentization strategy (#88 F5): peel cohesive sub-views
    off the God-object one at a time, under a moratorium on new
    full-screen-at-once renders.

    Owns the Profiles pivot's **entire rendering** (slices 1-2) and its
    **grid-editing model** (slice 3): the state -- ``grid`` (pending edits),
    ``applied`` (last-applied snapshot), ``pcol`` (cursor column),
    ``targets``/``host_cols`` (the matrix axes), ``_prof_unavailable`` --
    plus the pure grid behaviour (``grid_dirty``/``pending_count``/
    ``cell_locked``/``profiles_present``/``_column_sels``/``toggle_cell``).
    ``PickerScreen`` exposes thin ``@property``/method shims onto these so
    its existing call sites and the test suite address them unchanged. The
    Apply/load plumbing (``_apply_profiles``, ``_start_profiles_run``,
    ``_commit_applied_profiles``, the background column loader) still lives
    on the engine and reads this state through the shims, to be pulled in
    by a follow-up slice. A later slice makes this a focusable Textual
    widget.
    """

    def __init__(self, eng) -> None:
        self._eng = eng
        self.pcol = 0                 # Profiles matrix column cursor
        self.grid = {}                # (target_idx, host_idx) -> bool present
        self.applied = {}             # last-applied snapshot of the grid
        self.targets = []
        self.host_cols = list(_DEFAULT_HOST_COLS)   # config-bound in setup()
        self._prof_unavailable = set()  # host-col idxs that couldn't load (#1370)
        # Real per-host column load / Apply plumbing (own-column model). The
        # source hooks are bound in PickerScreen.setup(); fixtures/tests leave
        # them None and keep the seeded self·agent diagonal.
        self._prof_lock = threading.Lock()
        self._prof_load = None        # src.load_profile_column (real sources)
        self._prof_apply = None       # src.apply_profile_column (real sources)
        self._prof_loading = False    # columns are streaming in
        self._prof_loaded = False

    # ---- grid-editing model (state owned here; PickerScreen shims delegate) --
    def grid_dirty(self):
        return any(self.grid.get(k) != self.applied.get(k) for k in self.grid)

    def pending_count(self):
        return sum(1 for k in self.grid if self.grid.get(k) != self.applied.get(k))

    def profiles_present(self):
        return sum(1 for v in self.grid.values() if v)

    def cell_locked(self, ti, hi):
        """A machine always has a profile for THIS repo with itself as host
        (self · agent) -- force-checked, not editable."""
        t = self.targets[ti]
        _lbl, hm, he = self.host_cols[hi]
        return t["machine"] == hm and t["env"] == he and t["agent"]

    def _column_sels(self, hi):
        """Build the TargetSel list for host column ``hi`` from the grid."""
        out = []
        for ti in range(len(self.targets)):
            if self.grid.get((ti, hi)):
                out.append(self._target_sel(ti))
        return out

    def toggle_cell(self):
        """Toggle the focused grid cell (Space on a Profiles row). Reads the
        focused row from the engine's ``sel``; reports via the engine's status
        line. Locked / unavailable cells are read-only no-ops."""
        eng = self._eng
        if not self.host_cols:
            return                       # no profile hosts -> nothing to toggle
        if self.pcol in self._prof_unavailable:
            _lbl, hm, he = self.host_cols[self.pcol]
            eng.debug = (f"{hm} {he}: profiles unavailable "
                         "(remote unreachable or needs upgrade) · read-only")
            return
        ti = eng.sel[1]
        if self.cell_locked(ti, self.pcol):
            eng.debug = "self · agent profile is mandatory (locked)"
            return
        key = (ti, self.pcol)
        self.grid[key] = not self.grid.get(key, False)
        t = self.targets[ti]
        _lbl, hm, he = self.host_cols[self.pcol]
        host = f"{hm} {he}"
        eng.debug = (f"{'+' if self.grid[key] else '-'} {host} → {t['label']}"
                     " (pending Apply)")

    # ---- real per-host column load + Apply plumbing (own-column model) -------
    def _target_sel(self, ti):
        """The TargetSel a target row represents (kind = agent|shell)."""
        t = self.targets[ti]
        return profiles_mod.TargetSel(
            t["machine"], t["env"], "agent" if t["agent"] else "shell")

    def start_load(self):
        """Background-load every host's saved column into the grid.

        Read-only: each host's column is fetched (local in-process, remote over
        SSH) and projected onto the grid; ``applied`` is snapshotted to match so
        the freshly-loaded state reads as 'no pending changes'.
        """
        self._prof_loading = True

        def work():
            from . import profiles_io
            cols = {}
            for hi, (_lbl, hm, he) in enumerate(self.host_cols):
                try:
                    cols[hi] = self._prof_load(hm, he)
                except Exception:
                    cols[hi] = None   # load failure -> unmanaged (default column)
            with self._prof_lock:
                # Don't clobber edits the user already started before the load
                # resolved -- only project columns onto a pristine grid.
                if not self.grid_dirty():
                    unavail = set()
                    for hi, sels in cols.items():
                        if sels is profiles_io.UNAVAILABLE:
                            # Unreachable / too-old remote: we can't know its
                            # real column, so mark it read-only. Seed just the
                            # locked self-diagonal so the grid stays consistent;
                            # cells render as "?" and are never editable or
                            # applied (#1370).
                            unavail.add(hi)
                            for ti in range(len(self.targets)):
                                self.grid[(ti, hi)] = self.cell_locked(ti, hi)
                            continue
                        if sels is None:
                            # Unmanaged host: render the DEFAULT column
                            # (minimal per-agent + bare cross-machine; see
                            # profiles.is_default_on), replacing the retired
                            # emit-everything default. The user can still curate
                            # from here and Apply.
                            _hlbl, hm, he = self.host_cols[hi]
                            for ti in range(len(self.targets)):
                                self.grid[(ti, hi)] = (
                                    profiles_mod.is_default_on(
                                        self._target_sel(ti), hm, he)
                                    or self.cell_locked(ti, hi))
                            continue
                        keys = {s.key for s in sels}
                        for ti in range(len(self.targets)):
                            on = self._target_sel(ti).key in keys or \
                                self.cell_locked(ti, hi)
                            self.grid[(ti, hi)] = on
                    self._prof_unavailable = unavail
                    self.applied = dict(self.grid)
                self._prof_loading = False
                self._prof_loaded = True

        threading.Thread(target=work, name="profiles-load", daemon=True).start()

    def apply(self):
        """Open the Apply confirmation: list exactly which terminal profiles
        each changed host column will gain/lose, *before* touching anything.

        Confirming runs the per-host progress dialog (local writes mirror to the
        terminal profiles, remote writes go over SSH). This is destructive to
        the terminal app's profile list, so the confirm step (and its explicit
        add/remove diff) is mandatory -- the regeneration is not silent.
        """
        eng = self._eng
        if eng.mock_mode:
            # Mock mode (explicit dev sandbox): simulate the apply, no writes.
            self.applied = dict(self.grid)
            eng.debug = f"Applied · {self.profiles_present()} profiles (mock)"
            return
        if not callable(self._prof_apply):
            # Real launch but the source exposes no apply hook -- surface it
            # honestly instead of silently pretending success. The real data
            # sources always provide it; this guards a misconfigured source.
            eng.debug = "Apply unavailable · source has no profiles IO hook"
            return
        changed = []
        diffs = {}
        for hi in range(len(self.host_cols)):
            if hi in self._prof_unavailable:
                continue             # read-only column: never diffed or applied
            added, removed = [], []
            for ti in range(len(self.targets)):
                now = bool(self.grid.get((ti, hi)))
                was = bool(self.applied.get((ti, hi)))
                if now != was:
                    (added if now else removed).append(self._target_sel(ti))
            if added or removed:
                changed.append(hi)
                diffs[hi] = (added, removed)
        if not changed:
            eng.debug = "Apply · nothing changed"
            return
        # Migrated to a native Textual ``ModalScreen`` (#88 F4): the confirm is a
        # pushed screen that owns the stack, backdrop, focus, and key routing and
        # returns its verdict via ``dismiss(True|False)``. Confirming runs the
        # per-host progress dialog; cancelling is a no-op.
        cf = {"changed": changed, "diffs": diffs}

        def _after(confirmed):
            if confirmed:
                self._start_run(cf)
            else:
                eng.debug = "Apply cancelled"
        eng.app.push_screen(ProfConfirmScreen(cf, list(self.host_cols)), _after)

    def _start_run(self, cf):
        """Execute the confirmed Apply: one progress item per changed host.
        Drives the engine's shared maintenance progress/executor infrastructure
        (the same ``ProgressScreen`` all maintenance ops use)."""
        from . import maintenance
        eng = self._eng
        changed = cf["changed"]
        diffs = cf.get("diffs", {})
        n_add = sum(len(diffs[hi][0]) for hi in changed if hi in diffs)
        n_rem = sum(len(diffs[hi][1]) for hi in changed if hi in diffs)
        items, tasks = [], []
        for hi in changed:
            _lbl, hm, he = self.host_cols[hi]
            key = f"{hm}/{he}"
            items.append({"id4": key, "title": f"{hm} {he}",
                          "machine_env": f"{hm} {he}", "hi": hi,
                          "state": "pending"})
            tasks.append((key, self._make_apply_task(hm, he, hi)))

        eng.executor = maintenance.MaintenanceExecutor("profiles", tasks)
        eng.progress = {
            "verb": "Apply profiles", "op": "profiles",
            "scope": f"{len(changed)} host column(s)",
            "items": items, "recs": [], "picked": [],
            "ticks": 0, "steps": 3, "done": False, "armed": True,
            "include_unused": False, "include_conversations": False,
            "n_add": n_add, "n_rem": n_rem,
        }
        eng.executor.start()
        eng._open_progress()

    def _make_apply_task(self, machine, env, hi):
        """A progress task that writes one host column; ``ok`` drives ✓/✗."""
        sels = self._column_sels(hi)

        def _run():
            ok, detail = self._prof_apply(machine, env, sels)
            return {"ok": bool(ok), "reason": detail, "detail": detail}
        return _run

    def commit_applied(self):
        """After an Apply run, advance ``applied`` for every host that succeeded
        (matched by the executor's per-item DONE state). Called from the engine's
        progress-close path (``_key_progress``)."""
        p = self._eng.progress
        if not p or p.get("op") != "profiles":
            return
        for it in p["items"]:
            if it.get("state") == "done":
                hi = it["hi"]
                for ti in range(len(self.targets)):
                    self.applied[(ti, hi)] = self.grid.get((ti, hi), False)

    def build_chrome(self, add, width, sel):
        # Profiles lives under Configuration with its own host-column axes -- no
        # machine-scope row and no top button region, so its chrome is empty
        # (the Apply/Reset buttons ride the bottom of the data). Uniform with the
        # other views' build_chrome/build_data split (#88 NF3).
        return

    def build_data(self, add, width, sel):
        self.build(add, width, sel)

    def build(self, add, width, sel):
        """Emit the Profiles pivot body into ``add`` (the ``build_body`` VRow
        sink). Mirrors the former ``PickerScreen._build_profiles`` exactly."""
        if not self.host_cols:
            # No Profiles host columns -- no ``copilot`` machine in
            # machines.yaml exposes a native-terminal (windows/linux) env, so
            # ``_DEFAULT_HOST_COLS`` ([]) is the only fallback. Render a
            # placeholder instead of indexing the empty column list, which used
            # to raise IndexError in ``_visible_pcols`` when the operator
            # arrowed into the Profiles pivot (issue #149).
            add(Text(
                "  No profile hosts configured -- add a copilot machine with a "
                "windows/linux env to machines.yaml.",
                style=C_DIM,
            ))
            add(Text(""))
            add(self._profiles_button_row(width, sel == ("BTN", 0),
                                          self._eng.btn_idx),
                stop=("BTN", 0))
            return
        colw = self._col_widths()
        lblw = 30
        vis = self._visible_pcols(width, lblw)
        if vis is None:
            return self._transposed(add, width, sel)
        lo, hi, ml, mr = vis
        add(self._legend(width))
        hdr = Text(" " + "TARGET \\ HOST".ljust(lblw - 1), style=C_HEADER)
        hdr.append("‹" if ml else " ", style=C_HINT)
        for j in range(lo, hi + 1):
            if j > lo:
                hdr.append(" ")
            hdr.append_text(self._host_header_cell(j, colw[j]))
        hdr.append("›" if mr else " ", style=C_HINT)
        hdr.append(" " * max(0, width - hdr.cell_len))
        add(hdr, kind="colhdr")
        for ti, t in enumerate(self.targets):
            row_sel = sel == ("PR", ti)
            agent = t["agent"]
            base = "grey78" if agent else "grey54"
            r = Text(" ")
            # The active target row label gets the same subtle active shading the
            # active host column header uses, so both cursor coordinates read.
            lbl_text = self._tlabel(t, lblw - 1, base)
            if row_sel:
                lbl_text.stylize("on grey23")
            r.append_text(lbl_text)
            r.append(" ")
            for j in range(lo, hi + 1):
                if j > lo:
                    r.append(" ")
                locked = self.cell_locked(ti, j)
                ch, style = self._cell_visual(ti, j, locked)
                if row_sel and j == self.pcol:
                    style = "grey50 on grey23" if locked else C_SEL
                r.append(ch.center(colw[j]), style=style)
            r.append(" " * max(0, width - r.cell_len))
            add(r, stop=("PR", ti), data=t)
        add(Text(""))
        add(self._profiles_button_row(width, sel == ("BTN", 0),
                                      self._eng.btn_idx),
            stop=("BTN", 0))

    def _transposed(self, add, width, sel):
        """Too narrow for a grid: one host is the header, each target a
        space-toggleable checkbox row. ◀▶ switches which host you're editing.
        Mirrors the former ``PickerScreen._build_profiles_transposed`` exactly."""
        eng = self._eng
        _lbl, hm, he = self.host_cols[self.pcol]
        add(self._legend(width))
        head = Text(" HOST  ", style=C_HEADER)
        head.append(hm, style=eng._hl(C_HEADER, True, False))
        head.append(" ", style=eng._hl("", True, False))
        head.append(he, style=eng._hl(C_ENV.get(he, C_HEADER), True, False))
        head.append(f"   ‹ {self.pcol + 1}/{len(self.host_cols)} ›  ◀▶ host",
                    style=C_HINT)
        if self.pcol in self._prof_unavailable:
            head.append("  · unavailable (needs upgrade)", style=C_DISABLED)
        head.append(" " * max(0, width - head.cell_len))
        add(head, kind="colhdr")
        for ti, t in enumerate(self.targets):
            agent = t["agent"]
            base = "grey78" if agent else "grey54"
            locked = self.cell_locked(ti, self.pcol)
            ch, cstyle = self._cell_visual(ti, self.pcol, locked)
            present = self.grid.get((ti, self.pcol), False)
            box = f"[{ch}]" if (present or ch == "✗") else "[ ]"
            r = Text(" ")
            r.append(" " + box + " ", style=cstyle)
            r.append_text(self._tlabel(t, width - 8, base))
            if sel == ("PR", ti):
                r.stylize("grey50 on grey23" if locked else C_SEL)
            r.append(" " * max(0, width - r.cell_len))
            add(r, stop=("PR", ti), data=t)

    # ---- render helpers (owned by the component) ------------------------
    def _col_widths(self):
        return [max(len(f"{m} {e}"), 3) + 2 for _lbl, m, e in self.host_cols]

    def _host_header_cell(self, j, colw):
        """One Profiles host-column header: machine (config) name in the header
        slot color + env in its per-env color, centered to the column width.
        The active host (the column being edited) gets the SAME subtle shading
        the machine tabs use for an active-but-unfocused tab -- never the
        inversion cursor, since the real cursor lives in the grid cell."""
        _lbl, m, e = self.host_cols[j]
        active = j == self.pcol
        unavail = j in self._prof_unavailable
        name_base = C_DISABLED if unavail else ("bold white" if active else C_TABOFF)
        env_base = C_DISABLED if unavail else C_ENV.get(e, name_base)
        cell = Text()
        cell.append(m, style=self._eng._hl(name_base, active, False))
        cell.append(" ", style=self._eng._hl("", active, False))
        cell.append(e, style=self._eng._hl(env_base, active, False))
        pad = max(0, colw - cell.cell_len)
        left = pad // 2
        out = Text(" " * left)
        out.append_text(cell)
        out.append(" " * (pad - left))
        return out

    def _visible_pcols(self, width, lblw):
        """Which host columns are visible. Returns (lo, hi, more_left, more_right)
        windowed around the cursor column; or None if not even one column fits
        (caller switches to transposed mode)."""
        colw = self._col_widths()
        n = len(colw)
        if n == 0:
            return None                # no host columns -- caller placeholders
        if self.pcol >= n:
            self.pcol = n - 1          # host set shrank; clamp the cursor column
        avail = width - lblw - 4   # reserve for ‹ / › markers
        if avail < colw[self.pcol]:
            return None
        lo = hi = self.pcol
        used = colw[lo]
        while True:
            grew = False
            if hi + 1 < n and used + 1 + colw[hi + 1] <= avail:
                hi += 1
                used += 1 + colw[hi]
                grew = True
            if lo - 1 >= 0 and used + 1 + colw[lo - 1] <= avail:
                lo -= 1
                used += 1 + colw[lo]
                grew = True
            if not grew:
                break
        return lo, hi, lo > 0, hi < n - 1

    def _tlabel(self, t, w, base):
        """Target label: 'machine env · agent', env token colored, padded to w."""
        seg = Text()
        seg.append(f"{t['machine']} ", style=base)
        seg.append(t["env"], style=C_ENV.get(t["env"], base))
        seg.append(f" · {'agent' if t['agent'] else 'shell'}", style=base)
        if seg.cell_len < w:
            seg.append(" " * (w - seg.cell_len))
        return seg

    def _cell_visual(self, ti, j, locked):
        """(glyph_char, style) for a matrix cell, reflecting applied vs pending."""
        if j in self._prof_unavailable:
            # Column we couldn't load (unreachable / too-old remote): show an
            # "unknown" marker, not a fabricated selection (#1370).
            return "?", C_DISABLED
        present = self.grid.get((ti, j), False)
        applied = self.applied.get((ti, j), False)
        agent = self.targets[ti]["agent"]
        if locked:
            return "✓", "grey50"
        if present and applied:
            return "✓", (C_PULSE[self._eng.pulse] if agent else "#37b7ff")  # active
        if present and not applied:
            return "✓", "bold #ff9e3b"      # pending add (alternate highlight)
        if applied and not present:
            return "✗", "bold #ff5f5f"      # pending removal
        return "·", C_DIM                   # inactive

    def _profiles_button_row(self, width, focus, active_idx):
        eng = self._eng
        dirty = self.grid_dirty()
        if not dirty:
            active_idx = 0          # Reset is unavailable -> not selectable
        n = self.pending_count()
        t = Text("  ")
        # Apply
        if dirty:
            alabel = f" ✓ Apply ({n}) "
            astyle = C_BTN_SEL if (focus and active_idx == 0) else "bold black on #ff9e3b"
        else:
            alabel = " ✓ Applied "
            astyle = C_BTN_SEL if (focus and active_idx == 0) else "bold black on green"
        t.append(alabel, style=astyle)
        t.append("   ")
        # Reset (only meaningful when dirty)
        if not dirty:
            rstyle = "grey42 on grey15"
        else:
            rstyle = eng._btn_style(focus, active_idx == 1)
        t.append(" ↺ Reset ", style=rstyle)
        suffix = ("proposed changes are NOT active yet" if dirty
                  else "selection active · Enter applies from anywhere")
        if t.cell_len + 4 + len(suffix) <= width:
            t.append("    " + suffix, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def _legend(self, width):
        """One dim line keying the grid's agent/shell rows (and any unreachable
        column) to plain language, so a round-trip edit doesn't silently drop
        the bare-SSH ``shell`` profiles (#1369)."""
        t = Text("  ")
        t.append("agent", style=C_LABEL)
        t.append(" = launch worktree", style=C_DIM)
        t.append("   ")
        t.append("shell", style=C_MUTED)
        t.append(" = plain SSH login shell", style=C_DIM)
        if self._prof_unavailable:
            t.append("   ")
            t.append("?", style=C_DISABLED)
            t.append(" = remote unavailable (needs upgrade)", style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t
