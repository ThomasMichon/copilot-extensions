#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult

from .engine_helpers import C_BAND, C_BTN_SEL, C_DIM, C_DISABLED, C_ENV, C_FAINT, C_HINT, C_HINT_ON, C_LABEL, C_LOAD, C_META, C_PULSE, C_READY, C_SECTION, C_SPIN, C_TAB_ACTIVE, C_TAB_FOCUS_ON, C_TABOFF, C_WARN, VERSION, VRow, _size_mb
from .engine_regions import _PickerButtons, _PickerMachine, _PickerNativeData, _PickerPivots, _PickerSegment, _PickerStickyHeader

class PickerScreenRenderingMixin:
    def build_body(self, width):
        vrows = []
        cur_band = None
        cur_section = None

        def add(text, stop=None, kind=None, data=None, new_section=None):
            # ``new_section`` lets an extracted body component (e.g.
            # MaintenanceView, #88 F5) open a section without reaching into this
            # closure's ``cur_section``/``vrows``: it pins the section to the
            # label + the vrow index this row will occupy, exactly as the inline
            # branches do via ``cur_section = (label, len(vrows))``.
            nonlocal cur_section
            if new_section is not None:
                cur_section = (new_section, len(vrows))
            vr = VRow(text, stop, kind, data)
            vr.pin_band = cur_band
            vr.pin_section = cur_section
            vrows.append(vr)
            return vr

        sel = self.sel
        if self._kind() == "worktrees":
            self.worktrees_view.build(add, width, sel)
        elif self._kind() == "maintenance":
            self.maintenance_view.build(add, width, sel)
        elif self._kind() == "registered":
            self.tasks_view.build(add, width, sel)
        else:
            self.profiles_view.build(add, width, sel)
        return vrows
    def _current_view(self):
        return {
            "worktrees": self.worktrees_view,
            "maintenance": self.maintenance_view,
            "registered": self.tasks_view,
        }.get(self._kind(), self.profiles_view)
    def _render_sig(self, W, H):
        """A cheap fingerprint of everything the derived frame / split depend on
        (#169). Two sibling segment widgets rendering in one paint pass share the
        same signature, so the memoized frame is built once and reused; any state
        change (a nav, a scroll, a reload, a machine/pivot switch, a direct
        attribute set from the capture seam) shifts the signature and forces a
        single rebuild. Mirrors the native list's own data signature, widened
        with the focus-cursor state (``sel`` / ``btn_idx`` / ``pcol``) and scroll
        offsets the chrome + body window also key on."""
        try:
            kind = self._kind()
            if kind == "registered":
                nrows = len(self._task_rows())
            elif kind == "maintenance":
                nrows = len(self.maint_records())
            else:
                nrows = len(self.list_records())
        except Exception:
            kind, nrows = None, -1
        wt = tuple(sorted(self.wt_sel.ids)) if hasattr(self, "wt_sel") else ()
        return (W, H, self.sel, self.top, self._data_top, kind, self.htab,
                self.machine_idx, self.btn_idx, getattr(self, "pcol", 0),
                nrows, wt, getattr(self, "pulse", 0),
                getattr(self, "update_state", None),
                getattr(self, "manager_update_state", None),
                getattr(self, "debug", None),
                self.cmd_mode, self.list_view.query, self.list_view.sort_index)
    def _build_body_split(self, width, sel=None):
        """Return ``(chrome_vrows, data_vrows)`` for the current pivot -- the
        fixed machine-scope + button chrome vs the scrolling data -- by driving
        the view's ``build_chrome`` / ``build_data`` emitters into two separate
        VRow sinks (#88 NF3). Concatenated, the rendered rows equal
        ``build_body(width)`` byte-for-byte; the split lets the compose tree
        render the chrome fixed and scroll only the data. ``sel`` defaults to the
        live ``self.sel``; the native OptionList body (#88 NF5-5) passes a
        sentinel sel so no focus-cursor highlight is baked into the row text (the
        native widget owns the cursor)."""
        use_sel = self.sel if sel is None else sel
        H = self.size.height or 30
        key = (self._render_sig(width, H), use_sel)
        cache = self._split_cache
        if cache is not None and cache[0] == key:
            return cache[1]

        def make_sink():
            vrows = []
            section = {"cur": None}

            def add(text, stop=None, kind=None, data=None, new_section=None):
                if new_section is not None:
                    section["cur"] = (new_section, len(vrows))
                vr = VRow(text, stop, kind, data)
                vr.pin_section = section["cur"]
                vrows.append(vr)
                return vr
            return vrows, add

        view = self._current_view()
        chrome, add_c = make_sink()
        view.build_chrome(add_c, width, use_sel)
        data, add_d = make_sink()
        view.build_data(add_d, width, use_sel)
        self._split_cache = (key, (chrome, data))
        return chrome, data
    def _build_data_vrows(self, width, sel=None):
        """Data-only VRows for the current pivot (no chrome) -- used by the
        native OptionList body (#88 NF5-5), which renders the chrome separately
        and must not drive ``build_chrome`` (e.g. ``tab_bar``) before the screen
        is set up. ``sel`` defaults to ``self.sel``; a sentinel suppresses the
        focus-cursor highlight so the native widget owns the cursor."""
        use_sel = self.sel if sel is None else sel
        vrows = []
        section = {"cur": None}

        def add(text, stop=None, kind=None, data=None, new_section=None):
            if new_section is not None:
                section["cur"] = (new_section, len(vrows))
            vr = VRow(text, stop, kind, data)
            vr.pin_section = section["cur"]
            vrows.append(vr)
            return vr

        self._current_view().build_data(add, width, use_sel)
        return vrows
    def _build_chrome_vrows(self, width, sel=None):
        """Chrome-only VRows (machine-scope + button rows) -- no data body.

        The interactive path's chrome segment widgets (``_PickerMachine`` /
        ``_PickerButtons``) use THIS instead of ``_build_body_split`` so a pure
        navigation (a highlight/``sel`` move) repaints only the small fixed
        chrome and NEVER rebuilds the scrolling data body. The live body is the
        native OptionList (``_PickerNativeData``), which owns its own cursor and
        diffs cheaply; rebuilding every data row here on each arrow keypress was
        a ~100ms/key stall that saturated the input queue under key-repeat and
        read as an unrecoverable freeze (dotfiles#948 follow-up). Cached by the
        same ``_render_sig`` key so the two chrome widgets in one paint pass
        share a single build; ``refresh()`` busts it alongside the other caches.
        """
        use_sel = self.sel if sel is None else sel
        H = self.size.height or 30
        key = (self._render_sig(width, H), use_sel)
        cache = self._chrome_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        vrows = []
        section = {"cur": None}

        def add(text, stop=None, kind=None, data=None, new_section=None):
            if new_section is not None:
                section["cur"] = (new_section, len(vrows))
            vr = VRow(text, stop, kind, data)
            vr.pin_section = section["cur"]
            vrows.append(vr)
            return vr

        self._current_view().build_chrome(add, width, use_sel)
        self._chrome_cache = (key, vrows)
        return vrows
    def _ensure_data_visible(self, data_vrows, data_h):
        """Scroll the data-body (compose tree) so the selected data row is
        visible; a no-op for chrome selections (they live in the fixed chrome and
        aren't in ``data_vrows``, so ``_stop_line`` returns 0) (#88 NF3)."""
        line = self._stop_line(data_vrows, self.sel)
        if line < self._data_top:
            self._data_top = line
        elif line >= self._data_top + data_h:
            self._data_top = line - data_h + 1
        self._data_top = max(0, min(self._data_top,
                                    max(0, len(data_vrows) - data_h)))
    def _data_sticky(self, data_vrows, data_h):
        """Pin the current section header at the top of the scrolled data body
        (mirrors ``_sticky`` over the data-relative offset)."""
        if self._data_top <= 0 or self._data_top >= len(data_vrows):
            return []
        first = data_vrows[self._data_top]
        ps = first.pin_section
        if ps and ps[1] < self._data_top:
            return [self._pin_line(ps[0], "section")]
        return []
    def _data_lines(self, data_vrows, data_h):
        """The windowed data-body rows for the compose tree's data widget --
        scroll + sticky over just the data rows (the fixed chrome renders
        separately above), padded to ``data_h`` (#88 NF3)."""
        return [vr.text if isinstance(vr, VRow) else vr
                for vr in self._data_window(data_vrows, data_h, pad=True)]
    def _data_window(self, data_vrows, data_h, pad=False):
        """The concrete window of data VRows (with the sticky section header
        substituted at the top when scrolled) -- shared by ``_data_lines`` (render)
        and ``_data_stop_at`` (pointer hit-testing) so a click maps to exactly the
        row that was drawn (#88 NF4)."""
        self._ensure_data_visible(data_vrows, data_h)
        window = data_vrows[self._data_top: self._data_top + data_h]
        sticky = self._data_sticky(data_vrows, data_h)
        for i, s in enumerate(sticky):
            if i < len(window):
                window[i] = s
            else:
                window.append(s)
        if pad:
            window = list(window)
            while len(window) < data_h:
                window.append(Text(""))
        return window
    def _data_stop_at(self, data_vrows, data_h, y):
        """The ``sel`` stop of the data row drawn at visible offset ``y`` in the
        data body, or ``None`` (blank line, sticky/section header, out of range)
        -- for click-to-select (#88 NF4)."""
        window = self._data_window(data_vrows, data_h)
        if 0 <= y < len(window):
            vr = window[y]
            stop = getattr(vr, "stop", None)
            if stop is not None:
                return stop
        return None
    def _chrome_split(self, chrome):
        """Split the pivot's chrome vrows into ``(machine_vrows, button_vrows)``
        at the button row, so the machine-scope and button regions can be
        separate focusable widgets (#88 NF3). Pivots without a top button region
        (Tasks) return all-machine + empty buttons."""
        bi = next((i for i, vr in enumerate(chrome)
                   if vr.stop == ("BTN", 0)), None)
        if bi is None:
            return chrome, []
        return chrome[:bi], chrome[bi:]
    def _widget_for_zone(self, zone):
        return self._ZONE_WIDGET.get(zone, "nf-body-data")
    def _nf_initial_focus(self):
        """Place the initial region focus on the widget that owns the default
        ``sel`` and arm the focus bridge (#88 NF3). Runs after the region widgets
        mount; guarded so it sets focus without a sel round-trip."""
        self._nf_syncing = True
        try:
            wid = self._widget_for_zone(self.sel[0])
            try:
                self.query_one(f"#{wid}").focus()
            except Exception:
                pass
        finally:
            self._nf_syncing = False
        self._nf_mounted = True
    def _sync_focus_to_sel(self):
        """Mirror native focus onto the region widget that owns the current
        ``sel`` (the other half of the on_focus -> sel bridge), so Tab/arrow
        navigation through the manual model keeps the framework's focus in step
        (#88 NF3). Guarded so the resulting ``focus()`` can't recurse."""
        wid = self._widget_for_zone(self.sel[0])
        try:
            w = self.query_one(f"#{wid}")
        except Exception:
            return
        if getattr(w, "can_focus", False) and self.app.focused is not w:
            self._nf_syncing = True
            try:
                w.focus()
            finally:
                self._nf_syncing = False
    def _hl(self, base, selected, focused):
        """Augment a base style with the selection highlight: the inversion
        cursor when the machine region is focused, a subtle bg when merely the
        active tab. Applied per-span so a highlight can be non-contiguous."""
        if not selected:
            return base
        extra = "reverse bold" if focused else "on grey23"
        return f"{base} {extra}".strip()
    def _machine_group(self, group, focused):
        """Render one machine's tab block: the machine (config) name once, then
        each of its environments as 'env marker', joined by ' · '. The name and
        the *selected* env+marker carry the highlight; sibling envs do not -- so
        selecting a secondary env shows a deliberate break in the highlight.
        Machine name takes the slot color (grey when idle, white when active);
        each env keeps its assigned per-env color."""
        first = self.machines[group[0]]
        label0, m0 = first[0], first[1]
        t = Text(" ")
        if label0 == "All":
            i = group[0]
            sel = i == self.machine_idx
            base = "bold white" if sel else C_TABOFF
            t.append("All", style=self._hl(base, sel, focused))
            t.append(" ")
            return t
        tab0 = self.source_tabs[group[0]] if self.source_tabs else {}
        if tab0.get("source_kind") not in (None, "machine-ssh"):
            i = group[0]
            state = self.machine_state(i)
            sel = i == self.machine_idx
            label_base = C_DISABLED if state == "disabled" else (
                "bold white" if sel else C_TABOFF
            )
            t.append(label0, style=self._hl(label_base, sel, focused))
            t.append(" ")
            if state == "disabled":
                marker, marker_style = "-", C_DISABLED
            elif state == "loading":
                marker, marker_style = self.spin(), C_LOAD
            elif state == "failed":
                marker, marker_style = "✗", C_WARN
            else:
                marker, marker_style = "✓", C_READY
            t.append(marker, style=self._hl(marker_style, sel, focused))
            t.append(" ")
            return t
        group_sel = any(i == self.machine_idx for i in group)
        all_disabled = all(self.machine_state(i) == "disabled" for i in group)
        name_base = (C_DISABLED if all_disabled
                     else "bold white" if group_sel else C_TABOFF)
        t.append(m0, style=self._hl(name_base, group_sel, focused))
        for n, i in enumerate(group):
            e = self.machines[i][2]
            state = self.machine_state(i)
            sel = i == self.machine_idx
            t.append(" ") if n == 0 else t.append(" · ", style=C_DIM)
            env_base = C_DISABLED if state == "disabled" else C_ENV.get(e, C_TABOFF)
            t.append(e, style=self._hl(env_base, sel, focused))
            t.append(" ", style=self._hl("", sel, focused))
            if state == "disabled":
                # #1289: a reliably single-width ASCII glyph (the old empty-set
                # marker U+2205 rendered ~1.5 cells on some terminals, shifting
                # the tab bar).
                mk, mkb = "-", C_DISABLED
            elif state == "loading":
                mk, mkb = self.spin(), C_LOAD
            elif state == "failed":
                mk, mkb = "✗", C_WARN
            else:
                mk, mkb = "✓", C_READY
            t.append(mk, style=self._hl(mkb, sel, focused))
        t.append(" ")
        return t
    def tab_bar(self, width, focused):
        # Group consecutive flat entries by machine (the 'All' entry stands
        # alone) so a multi-env machine renders its name once.
        groups = []
        prev = None
        for i, (label, m, e, ok) in enumerate(self.machines):
            tab = self.source_tabs[i] if self.source_tabs else {}
            group_key = (
                m
                if tab.get("source_kind") in (None, "machine-ssh")
                else tab.get("source_id")
            )
            if label != "All" and group_key == prev:
                groups[-1].append(i)
            else:
                groups.append([i])
            prev = group_key if label != "All" else None
        blocks = [self._machine_group(g, focused) for g in groups]
        blen = [b.cell_len for b in blocks]
        ng = len(groups)
        selg = next((gi for gi, g in enumerate(groups) if self.machine_idx in g), 0)
        gap = 3
        budget = width - 6   # reserve for ‹ / › overflow markers
        lo = hi = selg
        used = blen[selg]
        while True:
            grew = False
            if hi + 1 < ng and used + gap + blen[hi + 1] <= budget:
                hi += 1
                used += gap + blen[hi]
                grew = True
            if lo - 1 >= 0 and used + gap + blen[lo - 1] <= budget:
                lo -= 1
                used += gap + blen[lo]
                grew = True
            if not grew:
                break
        t = Text(" ")
        t.append("‹ " if lo > 0 else "  ", style=C_HINT)
        for k in range(lo, hi + 1):
            if k > lo:
                t.append(" " * gap)
            t.append_text(blocks[k])
        t.append(" ›" if hi < ng - 1 else "  ", style=C_HINT)
        t.append(" " * max(0, width - t.cell_len))
        return t
    def status_text(self, compact=False):
        t = Text()
        if self._kind() == "worktrees":
            secs = dict((lbl, len(rows)) for lbl, rows in self.current_list()[1])
            a = secs.get("Active", 0)
            r = secs.get("Recent", 0)
            c = secs.get("Completed", 0)
            u = sum(n for lbl, n in secs.items() if lbl.startswith("Unowned"))
            t.append("●", style=C_PULSE[self.pulse])
            if compact:
                t.append(f"{a} ", style=C_LABEL)
                t.append(f"◷{r} ✓{c}", style=C_FAINT)
                if u:
                    t.append(f" ?{u}", style=C_FAINT)
            else:
                t.append(f" {a} active", style=C_LABEL)
                t.append(" · ", style=C_DIM)
                t.append(f"{r} recent", style=C_META)
                t.append(" · ", style=C_DIM)
                t.append(f"{c} done", style=C_META)
                if u:
                    t.append(" · ", style=C_DIM)
                    t.append(f"{u} unowned", style=C_META)
        elif self._kind() == "maintenance":
            rows = self.cleanup_rows()
            mib = sum(_size_mb(w) for w in rows)
            if compact:
                t.append(f"⌫ {len(rows)}", style=C_META)
            else:
                t.append(f"{len(rows)} candidates · ~{mib} MiB", style=C_META)
        elif self._kind() == "registered":
            reg = self._reg_pivot()
            state, rows, _err = self._task_state()
            glyph = self.spin() if state == "loading" else "◆"
            t.append(f"{glyph} ", style=C_LOAD if state == "loading" else C_FAINT)
            label = reg.label if reg else "tasks"
            t.append(f"{len(rows)}{'' if compact else ' ' + label.lower()}", style=C_LABEL)
        else:
            n = self.profiles_present()
            t.append("⚙ ", style=C_FAINT)
            t.append(f"{n}{' set' if not compact else ''}", style=C_LABEL)
        # Live mode: surface how many machines are still loading / failed so the
        # All view's streaming fill-in is legible.
        if self.live and self.loader is not None and self._kind() == "worktrees":
            # Only the loading-spinner count is surfaced. A machine-load (SSH)
            # failure is NOT a failed worktree -- the per-machine tab already
            # shows its ✗ -- so totalling them here only confused (test-chamber
            # #1347).
            _ready, loading, _failed = self.loader.counts()
            if loading:
                t.append(f"  {self.spin()}{loading}", style=C_LOAD)
                if not compact:
                    t.append(" loading", style=C_LOAD)
        return t
    def _top_pad(self, W, more_above, scrolled):
        left = f"▲ {scrolled} more above" if more_above else "▲"
        lstyle = C_HINT_ON if more_above else C_HINT
        use = self.status_text(False)
        if 1 + len(left) + 3 + use.cell_len + 1 > W:
            use = self.status_text(True)
        t = Text(" ")
        t.append(left, style=lstyle)
        gap = W - t.cell_len - use.cell_len - 1
        t.append(" " * max(1, gap))
        t.append_text(use)
        t.append(" " * max(0, W - t.cell_len))
        return t
    def _frame_segments(self):
        """Compute the four screen segments -- header / chrome / body / footer --
        as lists of ``Text`` line-rows, plus the working width ``W`` (#88 NF2).

        This is the single source of the picker's screen content. The monolithic
        ``render()`` flattens the four segments in order (byte-identical to the
        pre-NF2 output); the NF2 compose skeleton (behind
        ``AGENT_WORKTREES_PICKER_NF``) has each child leaf widget render its own
        segment from this same dict, so the two paths are guaranteed identical.
        """
        W = self.size.width or 100
        H = self.size.height or 30
        key = self._render_sig(W, H)
        cache = self._frame_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        top = self.topbar(W)              # [title, htabs]  (2 rows)
        foot = self.footer(W)
        # chrome rows: title, htabs, header-border, stats, bottom-border, footer
        body_h = max(1, H - len(top) - 4)
        vrows = self.build_body(W)
        self._ensure_visible(vrows, body_h)
        window = vrows[self.top: self.top + body_h]
        sticky = self._sticky(vrows, body_h)
        for i, s in enumerate(sticky):
            if i < len(window):
                window[i] = s
            else:
                window.append(s)
        body_lines = [vr.text if isinstance(vr, VRow) else vr for vr in window]
        while len(body_lines) < body_h:
            body_lines.append(Text(""))
        more_above = self.top > 0
        below = len(vrows) - (self.top + body_h)
        header_border = self._border_row(W, "▲", more_above)
        bottom_border = self._border_row(W, "▼", below > 0)
        stats = self._stats_row(W)
        seg = {
            "W": W,
            "header": list(top),
            "chrome": [header_border, stats],
            "body": body_lines,
            "footer": [bottom_border, foot],
        }
        self._frame_cache = (key, seg)
        return seg
    @staticmethod
    def _join_lines(lines, W):
        """Join screen rows into a single ``Text``, cropping each to ``W`` so a
        line never wraps (shared by ``render()`` and the NF2 segment widgets)."""
        out = Text()
        for i, ln in enumerate(lines):
            if i:
                out.append("\n")
            lt = ln if isinstance(ln, Text) else Text(str(ln))
            lt.truncate(W, overflow="crop")   # never let a line wrap
            out.append_text(lt)
        return out
    def compose(self) -> ComposeResult:
        # NF5 (#88): the native compose tree is the *sole display path*. The
        # screen is a container of segment/region widgets -- a passive title +
        # chrome + footer view, and the focusable pivots / machine-scope / button
        # / data regions (native Tab moves between them; on_focus mirrors ``sel``;
        # the machine + button chrome renders fixed above the scrolling data).
        # ``render()`` is retained NOT as a display path but as the deterministic
        # *capture* seam (capture.py, the golden, the picker-snapshot A/B tool,
        # the picker vision's auditable-rendering); it and these widgets both
        # derive from ``_frame_segments`` / ``_build_body_split``, so they cannot
        # drift.
        yield _PickerSegment(self, "header", slice(0, 1), id="nf-title")
        yield _PickerPivots(self, id="nf-pivots")
        yield _PickerSegment(self, "chrome", id="nf-chrome")
        yield _PickerMachine(self, id="nf-machine")
        yield _PickerButtons(self, id="nf-buttons")
        # NF5-5 (#88): the native OptionList data body is the only remaining
        # body path, with a pinned section header above it (hidden until
        # scrolled).
        yield _PickerStickyHeader(id="nf-body-sticky")
        yield _PickerNativeData(self, id="nf-body-data")
        yield _PickerSegment(self, "footer", id="nf-footer")
    def _refresh_nf_segments(self) -> None:
        """Propagate a screen state change to the child segment/region widgets
        (their ``render()`` reads back off this screen). The native OptionList
        data body (#88 NF5-5) rebuilds its options on demand instead."""
        for seg_id in ("nf-title", "nf-pivots", "nf-chrome", "nf-machine",
                       "nf-buttons", "nf-body-data", "nf-footer"):
            try:
                w = self.query_one(f"#{seg_id}")
                if isinstance(w, _PickerNativeData):
                    w.refresh_data()
                else:
                    w.refresh()
            except Exception:
                pass
    def refresh(self, *args, **kwargs):
        # Keep the NF2 segment widgets in step with the screen: any state change
        # that refreshes the screen must re-render the child segments too (they
        # read off this screen). A no-op when the skeleton is disabled.
        # Bust the per-refresh render caches (#169) first: a refresh means the
        # screen state may have changed, so the memoized frame/split must be
        # recomputed once and then shared by every segment widget in this pass.
        self._frame_cache = None
        self._split_cache = None
        self._chrome_cache = None
        result = super().refresh(*args, **kwargs)
        self._refresh_nf_segments()
        return result
    def _border_row(self, W, arrow, active):
        """A separator line carrying a centered scroll arrow with a blank space
        either side: ────── ▲ ──────. Arrow glows when there's more that way."""
        left = (W - 3) // 2
        rightn = W - 3 - left
        t = Text("─" * max(0, left), style=C_DIM)
        t.append(" ")
        t.append(arrow, style=C_HINT_ON if active else C_HINT)
        t.append(" ")
        t.append("─" * max(0, rightn), style=C_DIM)
        return t
    def _stats_row(self, W):
        """Left: the region's sub-pivot hint (◀ machine ▶ etc). Right: the
        section counts (with a glyph-compact fallback when narrow)."""
        hint = "Host  ◀▶" if self._kind() == "profiles" else "Machine  Ctrl ◀▶"
        use = self.status_text(False)
        if 1 + len(hint) + 3 + use.cell_len + 1 > W:
            use = self.status_text(True)
        t = Text(" ")
        t.append(hint, style=C_DIM)
        gap = W - t.cell_len - use.cell_len - 1
        t.append(" " * max(1, gap))
        t.append_text(use)
        t.append(" " * max(0, W - t.cell_len))
        return t
    def _hint_row(self, W, direction, active, count):
        arrow = "▲" if direction == "up" else "▼"
        t = Text()
        if active:
            label = f"{arrow}  {count} more above" if direction == "up" \
                else f"{arrow}  {count} more below"
            pad = (W - len(label)) // 2
            t.append(" " * max(0, pad))
            t.append(label, style=C_HINT_ON)
        else:
            pad = (W - 1) // 2
            t.append(" " * max(0, pad))
            t.append(arrow, style=C_HINT)
        t.append(" " * max(0, W - t.cell_len))
        return t
    def _manager_update_seg(self):
        """Render the Manager's OWN update-availability state -- distinct
        from :meth:`_update_seg` (the engine/marketplace payload's staged
        state). Directly qualifies the ``v{VERSION}`` string it sits next
        to: ``idle`` shows nothing (never checked yet / non-GitHub source),
        ``current`` shows a plain ✓, ``available`` names the newer version
        and the command to fetch it (no in-picker apply flow -- unlike the
        engine's staged-update refresh, updating the Manager itself needs a
        real network fetch, so this stays purely informational)."""
        st = getattr(self, "manager_update_state", "idle")
        if st == "idle":
            return None
        t = Text()
        if st == "current":
            t.append(" ✓", style=C_READY)
        elif st == "available":
            from ... import manager_update_check as _muc

            remote = _muc.read_status().get("remote_version") or "newer"
            t.append(" ↻ ", style=C_HINT_ON)
            t.append(f"Manager update available ({remote}) -- run "
                     "`worktree-manager update`", style=C_HINT_ON)
        return t
    def _update_seg(self, focused: bool):
        """Render the launcher's idle/paused/checking/current/available state."""
        st = getattr(self, "update_state", "idle")
        if st == "idle":
            return None
        t = Text()
        if st == "checking":
            t.append(" " + self.spin(), style=C_SPIN)
        elif st in ("current", "paused"):
            t.append(" ✓" if st == "current" else " ‖",
                     style=C_READY if st == "current" else C_DIM)
        elif st == "available":
            t.append(" ↻", style=(C_BTN_SEL if focused else C_HINT_ON))
        return t
    def topbar(self, W):
        # Right-side segments, dropped in this order as width shrinks:
        # version, branch, env, repo. Always kept: "Worktree Manager" + machine.
        ver = f" · v{VERSION}"
        m, e = self._src_local()
        host = m.lower() if m else "…"
        # Repo name + default branch are project config, surfaced by the data
        # source (data_local/data_ssh expose REPO/BRANCH from the resolved
        # config); never hardcoded. Empty when a source omits them (e.g. a
        # fixture source) so the segment is dropped rather than showing a
        # fabricated name.
        repo, branch = self._src_repo_branch()
        present = {"update_text": True, "version": True, "repo": bool(repo),
                   "env": bool(e), "branch": bool(branch)}
        upd_focused = self.sel[0] == "UPD"

        def build():
            left = Text(" Worktree Manager", style="bold")
            if present["version"]:
                left.append(ver, style=C_DIM)
            # Manager-self update indicator: qualifies the version string
            # directly above (distinct from the engine/marketplace segment
            # below -- see _manager_update_seg's own docstring for why they
            # must not be conflated).
            mgr_seg = self._manager_update_seg()
            if mgr_seg is not None:
                left.append_text(mgr_seg)
            # Update indicator (#1430): spinner while the launcher stages the
            # marketplace update, ✓ when current, ↻ when an update is staged.
            seg = self._update_seg(upd_focused)
            if seg is not None:
                left.append_text(seg)
                if present["update_text"] and self.update_state in ("available", "paused"):
                    left.append(" Update available…" if self.update_state == "available"
                                else " Updates paused",
                                style=(C_BTN_SEL if upd_focused else C_HINT_ON)
                                if self.update_state == "available" else C_DIM)
            right = Text("host ", style=C_DIM)
            right.append(host, style=C_META)
            if present["env"]:
                right.append(" · ", style=C_DIM)
                right.append(e, style=C_ENV.get(e, C_META))
            if present["repo"]:
                right.append(f"  ·  {repo}", style=C_DIM)
            if present["branch"]:
                right.append(f" · {branch}", style=C_DIM)
            return left, right

        for drop in ("update_text", "version", "branch", "env", "repo"):
            left, right = build()
            if left.cell_len + 1 + right.cell_len + 1 <= W:
                break
            present[drop] = False
        left, right = build()
        l1 = left
        gap = W - left.cell_len - right.cell_len - 1
        l1.append(" " * max(1, gap))
        l1.append_text(right)
        l1.append(" " * max(0, W - l1.cell_len))
        l2 = Text("  ")
        v_focus = self.sel[0] == "V"
        for n, i in enumerate(self._left_pivots()):
            label = self.htabs[i]
            if n:
                l2.append("     ")
            if i == self.htab:
                l2.append(label.upper(),
                          style=C_TAB_FOCUS_ON if v_focus else C_BAND)
            else:
                l2.append(label, style="white" if v_focus else C_TABOFF)
        # Right-aligned ⚙ Configuration entry hosting Profiles etc. (#1426). It
        # is active when the current pivot lives under it, focused when the
        # ("CFG", 0) stop holds the cursor.
        cfg = self._config_pivots()
        if cfg:
            cfg_focus = self.sel[0] == "CFG"
            cfg_active = self.htab in cfg
            cstyle = (C_TAB_FOCUS_ON if cfg_focus
                      else C_TAB_ACTIVE if cfg_active else C_TABOFF)
            chip = Text("⚙ Configuration", style=cstyle)
            l2.append(" " * max(2, W - l2.cell_len - chip.cell_len - 1))
            l2.append_text(chip)
            l2.append(" ")
        else:
            # Tab never switches the view -- it moves focus between regions. The
            # view pivot moves with ◀▶ (while focused here) or [ ]/^⇧◀▶ (#1344).
            hint = ("◀▶ switch view · ↓ body · Tab region "
                    if v_focus else "[ ] switch view ")
            l2.append(hint.rjust(max(1, W - l2.cell_len)), style=C_DIM)
        return [l1, l2]
    def _focus_hint(self):
        """Focus-specific footer hint: exactly what Enter/Space do for the
        current focus, naming the concrete target (#1344)."""
        zone = self.sel[0]
        if zone == "UPD":
            return ("Enter: apply staged update + restart the picker"
                    " · ↑↓ move · Tab region")
        if zone == "V":
            return (f"◀▶ view / ⚙ Config (on {self.htabs[self.htab]})"
                    f" · Enter: focus body · Tab region · [ ] view")
        if zone == "CFG":
            return ("◀▶ back to pivots · Enter: open Configuration"
                    " · ↑↓ move · Tab region")
        if zone == "M":
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
            return ""
        if zone == "L":
            rec = self._selected_record()
            wid = rec.get("id4") if rec else "?"
            nsel = len(self.wt_sel)
            if nsel:
                return (f"Space: select/deselect · Enter: actions for "
                        f"{nsel} selected · / filter · s sort"
                        f" · Tab region · ^◀▶ machine")
            return (f"Space: select · Enter: sub-menu for worktree {wid}"
                    f" · / filter · s sort · Tab region · ^◀▶ machine")
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
        else:
            hints = self._focus_hint()
        f = Text(" " + hints, style=C_META)
        if self._busy_label:
            # Always-async: while a background action runs, show the shared
            # animated spinner + its label (not a static line), so no control ever
            # looks like it did nothing. The tick keeps this at ~10 fps.
            dbg = f"· {self.spin()} {self._busy_label}… "
        else:
            dbg = f"· {self.debug} "
        f.append(dbg.rjust(max(1, W - f.cell_len)), style=C_DIM)
        if f.cell_len > W:
            f = Text(f.plain[:W])
        return f
    def _stop_line(self, vrows, stop):
        for i, vr in enumerate(vrows):
            if vr.stop == stop or (isinstance(vr.stop, list) and stop in vr.stop):
                return i
        return 0
    def _ensure_visible(self, vrows, body_h):
        line = self._stop_line(vrows, self.sel)
        if line < self.top:
            self.top = line
        elif line >= self.top + body_h:
            self.top = line - body_h + 1
        self.top = max(0, min(self.top, max(0, len(vrows) - body_h)))
    def _sticky(self, vrows, body_h):
        if self.top <= 0 or self.top >= len(vrows):
            return []
        first = vrows[self.top]
        pins = []
        pb = first.pin_band
        ps = first.pin_section
        if pb and pb[1] < self.top:
            pins.append(self._pin_line(pb[0], "band"))
        if ps and ps[1] < self.top:
            pins.append(self._pin_line(ps[0], "section"))
        return pins
    def _pin_line(self, label, kind):
        W = self.size.width or 100
        if kind == "band":
            t = Text(f" {label}", style=C_BAND)
        else:
            t = Text(f"  ── {label} ", style=C_SECTION)
        t.append(" " * max(0, W - t.cell_len))
        t.stylize("on grey15")  # subtle pinned background
        return t
    @staticmethod
    def _wrap_text(text: str, width: int) -> list[str]:
        """Word-wrap ``text`` to ``width`` columns; collapse blank lines.

        Preserves the transcript's own line breaks, then greedily wraps each
        line on whitespace. A single word longer than ``width`` is hard-split.
        """
        width = max(8, width)
        out: list[str] = []
        for raw in text.replace("\r", "").split("\n"):
            line = raw.rstrip()
            if not line:
                if out and out[-1] != "":
                    out.append("")
                continue
            cur = ""
            for word in line.split(" "):
                while len(word) > width:
                    if cur:
                        out.append(cur)
                        cur = ""
                    out.append(word[:width])
                    word = word[width:]
                if not cur:
                    cur = word
                elif len(cur) + 1 + len(word) <= width:
                    cur += " " + word
                else:
                    out.append(cur)
                    cur = word
            if cur:
                out.append(cur)
        return out or [""]
