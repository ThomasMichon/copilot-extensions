#!/usr/bin/env python3
"""List/body picker views extracted from ``engine.py``."""
from __future__ import annotations

from rich.text import Text

from . import derive
from .engine_helpers import (
    C_DIM,
    C_ENV,
    C_HEADER,
    C_LABEL,
    C_LOAD,
    C_PULSE,
    C_PULSE_AWAIT,
    C_SECTION,
    C_SEL,
    C_SEL_BG,
    C_SEL_ON,
    C_META,
    C_WARN,
    CLEAN_SPECS,
    MAINT_GROUP_ORDER,
    PAD,
    _NO_FLEX_COLUMN,
    _clip,
    _palette_style,
    _size_mb,
    fit,
    header_text,
    row_text,
)
from .selection import ListSelection

class MaintenanceView:
    """Encapsulated Maintenance-pivot body sub-view (#88 F5, slice 5a).

    A cohesive sub-view carved out of ``PickerScreen.build_body``, mirroring
    :class:`ProfilesView` per the incremental componentization strategy
    (#88 F5): peel cohesive sub-views off the God-object one at a time until
    the shared ``sel=(zone,index)`` focus model shrinks to just the chrome.

    Owns the Maintenance pivot's **rendering** (slice 5a -- ``build`` plus
    ``_selectall_row``/``_header``/``_group_row``/``_row``) and its
    **selection model** (slice 5b): ``maint_sel`` plus grouping/multi-select
    (``maint_groups``/``maint_records``/``_maint_ids``/``_toggle_maint``/
    ``_toggle_maint_all``/``_toggle_group``). ``PickerScreen`` exposes a
    ``maint_sel`` ``@property`` shim and one-line delegating methods so its
    call sites and the test suite address them unchanged. Reads
    engine-owned shared infrastructure via ``self._eng``: ``cleanup_rows``,
    the ``_checkbox`` glyph helper, the status line, tab bar, and button row.
    A later slice makes this a focusable Textual widget.
    """

    def __init__(self, eng) -> None:
        self._eng = eng
        self.maint_sel = ListSelection()   # Maintenance multi-select (#1345)

    # ---- Maintenance grouping + multi-select (#1345; moved here #88 F5 5b) ---
    def maint_groups(self):
        """Maintenance rows grouped by display state, in MAINT_GROUP_ORDER."""
        rows = self._eng.cleanup_rows()
        by = {}
        for r in rows:
            by.setdefault(r.get("state", "?"), []).append(r)
        groups = [(st, by.pop(st)) for st in MAINT_GROUP_ORDER if st in by]
        groups.extend(by.items())   # any unlisted states, first-seen order
        return groups

    def maint_records(self):
        """Flat Maintenance row list in grouped display order (indexes the
        ("C", i) stops -- must match build's ordering)."""
        out = []
        for _st, rows in self.maint_groups():
            out.extend(rows)
        return out

    def _maint_ids(self):
        return {r["id4"] for r in self.maint_records()}

    def _toggle_maint(self, i):
        recs = self.maint_records()
        if 0 <= i < len(recs):
            wid = recs[i]["id4"]
            now_on = self.maint_sel.toggle(wid)
            self._eng.debug = (f"{'selected' if now_on else 'deselected'}"
                               f" {wid} · {len(self.maint_sel)} selected")

    def _toggle_maint_all(self):
        ids = self._maint_ids()
        if self.maint_sel.toggle_all(ids):
            self._eng.debug = f"selected all · {len(ids)}"
        else:
            self._eng.debug = "cleared selection"

    def _toggle_group(self, gi):
        groups = self.maint_groups()
        if 0 <= gi < len(groups):
            st, rows = groups[gi]
            ids = {r["id4"] for r in rows}
            if self.maint_sel.toggle_all(ids):
                self._eng.debug = f"selected {st} · {len(ids)}"
            else:
                self._eng.debug = f"deselected {st} · {len(ids)}"

    # ---- Maintenance row rendering (#1345; moved here in #88 F5 slice 5a) ----
    def _selectall_row(self, width, focus):
        eng = self._eng
        ids = self._maint_ids()
        all_on = self.maint_sel.all_selected(ids)
        glyph, gc = eng._checkbox(all_on)
        nsel = self.maint_sel.count(ids)
        t = Text("  ")
        t.append(glyph, style=gc)
        t.append("  ")
        t.append("Select all", style=C_LABEL)
        t.append(f"   ({nsel}/{len(ids)} selected)", style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        if focus:
            t.stylize(C_SEL)
        return t

    def _header(self, ccols, width):
        # 4-cell checkbox gutter, then the normal column header.
        t = Text("    ")
        body = header_text(ccols, max(1, width - 4), indent=0)
        t.append_text(body)
        if t.cell_len < width:
            t.append(" " * (width - t.cell_len))
        return t

    def _group_row(self, state, rows, width, focus):
        eng = self._eng
        ids = {r["id4"] for r in rows}
        all_on = self.maint_sel.all_selected(ids)
        glyph, gc = eng._checkbox(all_on)
        t = Text("  ")
        t.append(glyph, style=gc)
        t.append("  ")
        head = Text(f"── {state} ", style=C_SECTION)
        head.append(f"({len(rows)}) ", style=C_DIM)
        t.append_text(head)
        t.append("─" * max(0, width - t.cell_len - 1), style=C_DIM)
        t.append(" ")
        if focus:
            t.stylize(C_SEL)
        return t

    def _row(self, d, ccols, width, selected, checked, pulse):
        glyph, gc = self._eng._checkbox(checked)
        t = Text(" ")
        t.append(glyph, style=gc)
        t.append("  ")
        body = row_text(d, ccols, max(1, width - 4), selected=False, pulse=pulse)
        t.append_text(body)
        if t.cell_len < width:
            t.append(" " * (width - t.cell_len))
        if selected:
            t.stylize(C_SEL)
        return t

    def build(self, add, width, sel):
        """Emit the Maintenance pivot body into ``add`` (the ``build_body`` VRow
        sink). Mirrors the former inline ``build_body`` maintenance branch
        exactly; group sections are opened via ``add(..., new_section=...)`` so
        this component never touches the closure's ``cur_section``/``vrows``.

        Split into ``build_chrome`` (the machine-scope + Cleanup/Sync button
        region) and ``build_data`` (the select-all row, column header, and the
        scrolling group/row list) so the concerns render into separate widgets;
        ``build`` emits both in order, byte-identically (#88 NF3)."""
        self.build_chrome(add, width, sel)
        self.build_data(add, width, sel)

    def build_chrome(self, add, width, sel):
        eng = self._eng
        btn_focus = sel == ("BTN", 0)
        add(eng.tab_bar(width, sel == ("M", 0)))
        recs = self.maint_records()
        total = sum(_size_mb(w) for w in recs)
        nsel = self.maint_sel.count(self._maint_ids())
        reclaim = sum(_size_mb(w) for w in recs if w["id4"] in self.maint_sel)
        suffix = f"{len(recs)} candidates · ~{total} MiB"
        if nsel:
            suffix += f"  ·  {nsel} selected · ~{reclaim} MiB"
        add(Text(""))
        add(eng.two_button_row(
            "Cleanup…", "Sync…", btn_focus, eng.btn_idx, suffix, width),
            stop=("BTN", 0))
        add(Text(""))

    def build_data(self, add, width, sel):
        eng = self._eng
        groups = self.maint_groups()
        # Checkbox column reserves 4 cells in front of the data columns.
        ccols = fit(CLEAN_SPECS, width - 1 - 4, "title", 12)
        add(self._selectall_row(width, sel == ("SA", 0)),
            stop=("SA", 0))
        add(self._header(ccols, width), kind="colhdr")
        li = 0
        for gi, (state, rows) in enumerate(groups):
            add(self._group_row(state, rows, width, sel == ("GH", gi)),
                stop=("GH", gi), kind="section",
                new_section=f"{state} ({len(rows)})")
            for rec in rows:
                d = dict(rec, mib=f"{_size_mb(rec)}M")
                checked = rec["id4"] in self.maint_sel
                add(self._row(d, ccols, width, sel == ("C", li),
                              checked, eng.pulse),
                    stop=("C", li), data=rec)
                li += 1

class WorktreesView:
    """Encapsulated Worktrees-list body sub-view (#88 F5, slice 7).

    The picker's **primary** body -- the machine's worktree list, grouped
    into Active/Recent/Completed/Unowned sections, with the multi-select
    checkbox gutter, per-row disposition/preview dimming, focus + selection
    highlight layering, and the decorative live worktree-status-core pulse
    sub-lines. Largest and most-coupled body, so carved out **last**, once
    the componentization pattern was proven three times over (Profiles /
    Maintenance / Tasks).

    This slice moves the **rendering** -- the whole Worktrees branch of
    ``build_body`` into ``build``. Because the list's multi-select **state**
    (``wt_sel``/``wt_anchor``) and its range/toggle behaviour thread deeply
    through the shared key-dispatch + focus machinery (``_dispatch_key``,
    ``_reconcile_wt_sel``, range-select, focus tracking) -- exactly the
    ``sel``/``stops`` chrome the *final* native-focus step addresses -- that
    state stays on ``PickerScreen`` and is read here via ``self._eng``,
    along with the list data (``current_list``/``list_records``), the
    multi-select predicates (``_wt_multiselect_active``/``_cleanable``),
    the shared ``_checkbox`` glyph, and the chrome rows
    (``tab_bar``/``new_worktree_row``/``active_button``). A later slice
    makes this a focusable Textual widget.
    """

    def __init__(self, eng) -> None:
        self._eng = eng

    def build(self, add, width, sel):
        """Emit the Worktrees-list body into ``add`` (the ``build_body`` VRow
        sink). Mirrors the former inline ``build_body`` worktrees branch exactly;
        group sections are opened via ``add(..., new_section=...)`` so this
        component never touches the closure's ``cur_section``/``vrows``.

        Split into ``build_chrome`` (the machine-scope + New/Clean/Sync button
        region -- the fixed chrome the NF3 focusable-region slices target) and
        ``build_data`` (the column header + the scrolling section/row list), so
        the two concerns can render into separate widgets; ``build`` emits both
        in order, byte-identically (#88 NF3)."""
        self.build_chrome(add, width, sel)
        self.build_data(add, width, sel)

    def build_chrome(self, add, width, sel):
        eng = self._eng
        btn_focus = sel == ("BTN", 0)
        add(eng.tab_bar(width, sel == ("M", 0)))
        if eng.cmd_mode or eng.list_view.query or eng.list_view.sort_index:
            add(eng._cmd_bar_row(width))
        if eng.button_set():
            add(Text(""))  # breathing room above the buttons
            add(eng.new_worktree_row(width, btn_focus, eng.btn_idx),
                stop=("BTN", 0))
            add(Text(""))  # breathing room below the buttons

    def build_data(self, add, width, sel):
        eng = self._eng
        btn_focus = sel == ("BTN", 0)
        cols, sections = eng.current_list_visible()
        # The checkbox gutter's two left cells (box + margin) are ALWAYS
        # reserved so the table never shifts. Since #88 NF5-5 (mouse support in
        # the native list) the box GLYPH is also always shown per row, so
        # multi-select is discoverable/clickable at rest (not only once a set is
        # being held).
        lcols = fit(cols, width - 2, _NO_FLEX_COLUMN, 0)
        add(header_text(lcols, width, indent=2), kind="colhdr")
        # Preview which worktrees an action targets, directly on the list:
        # when the Clean/Sync button is merely focused, dim the rows that
        # action can't touch. (The old live-filter preview driven by an open
        # Clean/Sync dialog was retired with #88 F4 -- that dialog is now a
        # centered ScopeDlgScreen carrying its own impact list.)
        preview = None
        preview_ids = None
        if btn_focus:
            ab = eng.active_button()
            preview = {"K": "clean", "SY": "sync"}.get(ab)
        li = 0
        for label, rows in sections:
            sec = Text(f"  ── {label} ", style=C_SECTION)
            sec.append("─" * (width - sec.cell_len), style=C_DIM)
            add(sec, kind="section", new_section=label)
            if not rows:
                add(Text("    (none)", style=C_DIM))
            for rec in rows:
                add(self._row_text(rec, li, sel, width, lcols,
                                   preview, preview_ids),
                    stop=("L", li), data=rec)
                add(self._detail_line(rec, width))
                for worker_line in self._worker_lines(rec, width):
                    add(worker_line)
                li += 1

    def _worker_lines(self, rec, width, limit=2):
        """One dim ``→ <venue> LIVE|IDLE <activity>`` line per remote worker
        this worktree supervises (venue-pivots-ux: supervised workers, seen
        from the worktree row), at most ``limit`` inline plus a ``+N more``
        tail. The activity is the transient half, so it is what gets clipped.
        Empty until a venue pivot has loaded (graceful absence)."""
        workers = self._eng._worktree_supervised_workers(rec)
        lines = []
        for worker in workers[:limit]:
            line = Text("      → ", style=C_DIM)
            line.append(worker["label"] or worker["pivot"], style=C_LABEL)
            live = worker["live"].upper()
            if live:
                line.append(" ")
                line.append(live, style=_palette_style("state", live) or C_DIM)
            if worker["activity"] and line.cell_len + 2 < width:
                line.append("  ")
                line.append(derive.truncate_text(worker["activity"], max(1, width - line.cell_len)),
                            style=C_DIM)
            if line.cell_len > width:
                line.truncate(width, overflow="ellipsis")
            lines.append(line)
        if len(workers) > limit:
            lines.append(Text(f"      → +{len(workers) - limit} more", style=C_DIM))
        return lines

    def _detail_line(self, rec, width):
        """The worktree row's second (detail) line: ``Title: Activity`` --
        the full/untruncated title as the OVERALL identity, plus its current
        ACTIVITY (a live pulse/intent when one is in flight, else the plain
        state label) as the scannable CURRENT status, both on one line
        (#6443 follow-up). Replaces the old raw ``status_markers``/
        ``asset_hints`` breakdown -- an operator-opaque closure-descriptor
        wire shorthand like ``C1 U* OC*`` rendered in red, which read as an
        error rather than routine bookkeeping. Any open/held claim (a
        non-empty ``status_markers`` or asset hint) now collapses to a single,
        neutrally-styled ``*`` at the end of the line -- the full claim/asset
        breakdown lives behind the Actions menu's "View details" card
        instead, so this line never has to fit an unbounded list.

        Also: a compact `` · <Phase>`` badge (see ``_worktree_claiming_task``,
        Phase 4 REVERSE cross-link) follows the state text, before `` *``."""
        title = str(rec.get("title") or "").strip() or "(untitled)"
        pulse = rec.get("live_pulse")
        intent = (rec.get("live_intent") or "").strip()
        markers = (rec.get("status_markers") or "").strip()
        assets = rec.get("asset_hints") or {}
        has_claims = bool(markers) or bool(assets.get("hints"))
        claim = self._eng._worktree_claiming_task(rec)
        phase_label = ""
        if claim:
            claim_row, group_field = claim
            if group_field:
                phase_label = str(claim_row.get(group_field) or "").strip()

        pline = Text("      ")
        badge_reserve = (3 + len(phase_label)) if phase_label else 0
        reserve = (2 if has_claims else 0) + badge_reserve  # trailing " *"
        pline.append(derive.truncate_text(title, max(1, width - pline.cell_len - reserve)),
                     style=C_LABEL)
        if pulse and intent:
            if pulse == "awaiting":
                pstyle, glyph = C_PULSE_AWAIT, "⏳ "
            else:
                pstyle = C_DIM if pulse == "fresh" else "grey30"
                glyph = "⟳ "
            pline.append(": ", style=C_DIM)
            pline.append(glyph, style=pstyle)
            avail = max(1, width - pline.cell_len - reserve)
            pline.append(derive.truncate_text(intent, avail), style=pstyle)
        else:
            state_label = str(rec.get("state") or "").strip().lower()
            if state_label:
                pline.append(": ", style=C_DIM)
                avail = max(1, width - pline.cell_len - reserve)
                pline.append(derive.truncate_text(state_label, avail), style=C_DIM)
        if phase_label and pline.cell_len + 3 + len(phase_label) <= width:
            pline.append(" · ", style=C_DIM)
            pline.append(phase_label, style=_palette_style("task_phase", phase_label) or C_DIM)
        if has_claims and pline.cell_len + 2 <= width:
            pline.append(" *", style=C_LABEL)
        # Hard width guarantee: even with the reserve above, truncate once at
        # the end so a pathological combination can never overflow the row.
        if pline.cell_len > width:
            pline.truncate(width, overflow="ellipsis")
        return pline

    def _row_text(self, rec, li, sel, width, lcols, preview, preview_ids):
        """Render one worktree row's fully-styled Text. Extracted from
        ``build_data`` so the native list's incremental checkbox repaint (#171)
        renders a single row through the SAME path as a full rebuild -- keeping
        the two byte-identical. ``sel`` drives the focus/selection highlight; the
        native list passes a sentinel (focus is the amber cursor, not baked in)."""
        eng = self._eng
        focused = sel == ("L", li)
        is_sel = eng._row_key(rec) in eng.wt_sel
        # Always show the per-row checkbox glyph (#88 NF5-5): with mouse support
        # the box is a discoverable, clickable multi-select affordance, so it
        # renders at rest rather than only when a set is already held.
        box = eng._checkbox(is_sel)
        txt = row_text(rec, lcols, width, False, pulse=eng.pulse, mark=box)
        if rec.get("hidden") and not focused:
            # Revealed bridge/system worktree -> dim it (#1422).
            txt.stylize("grey42")
        elif (preview_ids is not None and rec["id4"] not in preview_ids
              and not focused):
            txt.stylize("grey35")   # outside the net set
        elif preview and not focused and not (
            eng._cleanable(rec) if preview == "clean"
            else rec.get("ff_eligible")
        ):
            txt.stylize("grey35")   # out of this action's scope
        # Focus / selection highlight, layered last so it reads as one state
        # (#2258 follow-up): green invert = focused AND selected, plain invert =
        # focused only, grey background = selected but the cursor has moved off.
        if focused and is_sel:
            txt.stylize(C_SEL_ON)
        elif focused:
            txt.stylize(C_SEL)
        elif is_sel:
            txt.stylize(C_SEL_BG)
        return txt

class TasksView:
    """Encapsulated registered-pivot (Tasks) body sub-view (#88 F5, slice 6).

    A cohesive sub-view carved out of the picker's monolithic
    ``PickerScreen.build_body`` into its own component, mirroring
    :class:`ProfilesView` / :class:`MaintenanceView` and per the incremental
    componentization strategy (#88 F5).

    This slice moves the registered pivot's **rendering** -- the body entry
    (``build``) plus the two row helpers (``_status_row`` for the load/count/empty
    header line, ``_row`` for one task entry). The registered pivot is
    **read-only** (its task list is background-loaded by a
    ``RegisteredPivotRuntime``), so there is no editable state to move: the data
    helpers (``_task_state`` / ``_task_rows`` / ``_task_groups``) and the
    pivot-scoping context (``_reg_pivot`` / ``_pivot_machine`` /
    ``_pivot_machine_id`` / ``_pivot_runtime``, all shared with dispatch + the
    task action sub-menu) stay on ``PickerScreen`` and are read here via
    ``self._eng``. A later slice makes this a focusable Textual widget.
    """

    def __init__(self, eng) -> None:
        self._eng = eng

    # ---- Registered-pivot (Tasks) row rendering (moved here #88 F5 slice 6) --
    def _status_row(self, reg, state, rows, err, width):
        """The header line for a registered pivot: a count, a load spinner, or
        the empty/error hint."""
        eng = self._eng
        account = bool(getattr(reg, "account_scoped", False))
        machine = eng._pivot_machine()
        if machine is None:
            machine = eng._scope_label()
        # An account-scoped pivot (CodeSpaces) is a cross-machine shared resource:
        # its status counts items, not "on <machine>".
        where = "" if account else f" for {machine}"
        t = Text("  ")
        if state == "loading":
            t.append(f"{eng.spin()} ", style=C_LOAD)
            t.append(f"loading {reg.label.lower()}{where}…", style=C_LOAD)
        elif state == "error":
            t.append("✗ ", style=C_WARN)
            t.append(f"{reg.label} unavailable: {err or 'command failed'}", style=C_META)
        elif not rows:
            t.append(reg.empty_hint, style=C_DIM)
        else:
            t.append("●", style=C_PULSE[eng.pulse])
            tail = f" {reg.label.lower()}" if account else f" on {machine}"
            t.append(f" {len(rows)}{tail}", style=C_LABEL)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def _row(self, reg, rec, width, selected):
        """One task entry: title, then dim badges (labels) + subtitle."""
        title = str(rec.get(reg.title_field) or rec.get(reg.id_field) or "(untitled)")
        t = Text("    ")
        t.append(title, style="grey85" if not selected else C_HEADER)
        badges: list[str] = []
        for f in reg.badge_fields:
            v = rec.get(f)
            if isinstance(v, (list, tuple)):
                badges.extend(str(x) for x in v)
            elif v:
                badges.append(str(v))
        for b in badges:
            t.append(f"  [{b}]", style=C_ENV.get(b, "cyan"))
        if reg.subtitle_field:
            sub = rec.get(reg.subtitle_field)
            if sub:
                t.append(f"  · {sub}", style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        if selected:
            t.stylize(C_SEL)
        return t

    # ---- D1: declarative columns + summary rendering ----------------------
    @staticmethod
    def _col_width(col) -> int:
        """The effective render width for a declarative column: the manifest
        ``width`` when set, else a header-derived fallback (min 6) so a
        width-less column still renders sanely."""
        return col.width if col.width else max(6, len(col.header))

    #: Minimum width the flex column is guaranteed to keep when other columns
    #: are being dropped to make the row fit (mirrors the Worktrees list's own
    #: ``fit(..., "title", 14)`` / ``fit(..., "title", 12)`` calls).
    _FLEX_MIN = 12

    #: Reserved trailing width for the "+N" column-drop indicator
    #: (``_column_header``'s ``dropped`` param) when at least one column was
    #: dropped: a leading space plus up to 3 glyphs (``+99``). Without this
    #: reservation the flex column always absorbs 100% of the remaining
    #: width -- leaving zero spare for the indicator on every real render, so
    #: it would never actually show up.
    _DROP_INDICATOR_RESERVE = 4

    def _fitted_columns(self, reg, width):
        """``reg.columns`` fit to the render ``width`` via the SAME column-fit
        algorithm the Worktrees list uses (module-level ``fit()``), so a
        declarative pivot's row can never exceed its render width and silently
        line-wrap.

        The flex column is whichever declares ``key == "title"`` (falling back
        to the last declared column when none does) -- it never disappears,
        only shrinks or grows to absorb the remaining width. Every other
        column is dropped, highest-``priority``-number first, until the row
        fits; ``Column.priority`` defaults to declaration order (later columns
        drop before earlier ones) when the manifest doesn't set it explicitly.
        Returns a tuple of :class:`~.pivots.Column` (dropped columns excluded,
        the flex column's ``width`` replaced with its fitted value) in their
        original declared order -- a drop-in replacement for ``reg.columns``
        everywhere a fitted render is wanted.

        Two-pass: an unreserved trial fit first decides whether anything gets
        dropped at all. Only when it does is a second fit run against a
        narrower ``width`` (minus ``_DROP_INDICATOR_RESERVE``), so the flex
        column leaves the trailing room ``_column_header``'s ``+N`` indicator
        needs instead of silently consuming it. A trial fit that drops
        nothing returns as-is -- no reservation, no wasted space."""
        from dataclasses import replace

        cols = reg.columns
        if not cols:
            return cols
        flex_idx = next(
            (i for i, c in enumerate(cols) if c.key == "title"), len(cols) - 1
        )
        specs = [
            (
                c.key, c.header, self._col_width(c), c.align,
                c.priority if c.priority is not None
                else (1 if i == flex_idx else i + 2),
            )
            for i, c in enumerate(cols)
        ]
        flex_key = cols[flex_idx].key
        flex_min = min(self._FLEX_MIN, self._col_width(cols[flex_idx]))
        avail = max(1, width - 1)
        fitted = fit(specs, avail, flex_key, flex_min)
        if len(fitted) < len(specs):
            reserved = max(1, avail - self._DROP_INDICATOR_RESERVE)
            fitted = fit(specs, reserved, flex_key, flex_min)
        by_key = {c.key: c for c in cols}
        return tuple(replace(by_key[k], width=w) for k, _h, w, _a in fitted)

    def _summary_line(self, reg, summary, width):
        """The D1 ``summary`` header line: ``reg.summary_template`` with
        ``{token}``s filled from the provider's summary dict. Missing tokens
        degrade to empty (never raises). ``None`` when there is nothing to show."""
        if not reg.summary_template or not summary:
            return None

        class _Default(dict):
            def __missing__(self, k):
                return ""

        safe = _Default({k: ("" if v is None else str(v)) for k, v in summary.items()})
        try:
            text = reg.summary_template.format_map(safe)
        except (KeyError, IndexError, ValueError):
            text = reg.summary_template
        t = Text("  ")
        t.append(text, style=C_LABEL)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def _banner_line(self, summary, width):
        """A prominent alert line rendered from the provider's summary payload:
        the reserved ``banner_text`` (+ optional ``banner_level``:
        info|warn|error). Distinct from the D1 ``summary`` template line -- a
        styled, unmissable notice (e.g. an actionable missing-``codespace``-scope
        remedy, #980). ``None`` when the summary carries no banner."""
        if not summary:
            return None
        text = summary.get("banner_text")
        if not text:
            return None
        level = str(summary.get("banner_level") or "warn").strip().lower()
        icon, style = {
            "error": ("\u2717", f"bold {C_WARN}"),   # cross mark -> red (error)
            "warn": ("\u26a0", "#d7af00"),            # warning sign -> amber
            "info": ("\u2139", C_LABEL),              # info source -> grey
        }.get(level, ("\u26a0", "#d7af00"))
        t = Text("  ")
        t.append(f"{icon} ", style=style)
        t.append(str(text), style=style)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def _column_header(self, cols, width, dropped=0):
        """The column-header row for a columns-declaring pivot. ``cols`` is the
        already-fitted column set (see ``_fitted_columns``) -- never
        ``reg.columns`` directly, or a wide manifest silently line-wraps.

        ``dropped`` is the count of declared columns the fit algorithm removed
        to make the row fit (``len(reg.columns) - len(cols)``, the caller's to
        compute -- see ``_fitted_columns``). When positive, a compact ``+N``
        indicator is right-aligned at the end of the row, spare width
        permitting, so a genuinely empty column can be told apart from one the
        fit algorithm merely dropped at a narrow viewport. Silently omitted
        when there isn't room; the header row never wraps."""
        t = Text(" ")
        for i, col in enumerate(cols):
            if i:
                t.append(PAD)
            w = self._col_width(col)
            t.append(_clip(col.header.upper(), w, col.align), style=C_HEADER)
        pad = max(0, width - t.cell_len)
        if dropped > 0:
            indicator = f"+{dropped}"
            if pad > len(indicator):
                t.append(" " * (pad - len(indicator)))
                t.append(indicator, style=C_DIM)
                return t
        t.append(" " * pad)
        return t

    def _column_row(self, cols, rec, width, selected, worktree_field=None):
        """One entry rendered across the pivot's declarative columns.
        ``worktree_field``, when given, names the column shown from
        ``rec["_worktree_short"]`` (see ``_enrich_pivot_rows``) instead of
        the raw, far-longer real id."""
        t = Text(" ")
        for i, col in enumerate(cols):
            if i:
                t.append(PAD)
            w = self._col_width(col)
            if col.key == worktree_field and "_worktree_short" in rec:
                val = rec["_worktree_short"]
            else:
                val = rec.get(col.key, "")
            cell = _clip("" if val is None else str(val), w, col.align)
            # Per-value palette colouring (reusing the picker's own vocabulary)
            # takes precedence; the column's literal ``style`` is the fallback.
            style = _palette_style(col.palette, val) or (col.style or "")
            t.append(cell, style=style)
        t.append(" " * max(0, width - t.cell_len))
        if selected:
            t.stylize(C_SEL)
        return t

    def _column_subtitle(self, reg, rec, width):
        """An optional dim second metadata line under a column row (e.g. the
        claiming worktree + its title). Rendered from ``reg.subtitle_field`` when
        the entry supplies it; ``None`` otherwise. Non-selectable (decorative)."""
        if not reg.subtitle_field:
            return None
        sub = rec.get(reg.subtitle_field)
        if not sub:
            return None
        t = Text("     ")
        t.append(str(sub), style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def build(self, add, width, sel):
        """Emit the registered-pivot (Tasks) body into ``add`` (the
        ``build_body`` VRow sink). Mirrors the former inline ``build_body``
        registered branch exactly; group sections are opened via
        ``add(..., new_section=...)`` so this component never touches the
        closure's ``cur_section``/``vrows``.

        Split into ``build_chrome`` (the machine-scope row -- Tasks has no button
        region) and ``build_data`` (the status line + the scrolling task list) so
        the concerns render into separate widgets; ``build`` emits both in order,
        byte-identically (#88 NF3)."""
        self.build_chrome(add, width, sel)
        self.build_data(add, width, sel)

    def build_chrome(self, add, width, sel):
        eng = self._eng
        add(eng.tab_bar(width, sel == ("M", 0)))
        add(Text(""))

    def build_data(self, add, width, sel):
        eng = self._eng
        reg = eng._reg_pivot()
        state, rows, err = eng._task_state()
        add(self._status_row(reg, state, rows, err, width))
        # A prominent provider alert (e.g. the actionable missing-`codespace`-
        # scope remedy, #980), rendered from the summary's reserved banner_* keys
        # -- placed above the D1 summary line so it is unmissable, and shown even
        # when the list is empty (the scope-gap case).
        banner_line = self._banner_line(eng._task_summary(), width)
        if banner_line is not None:
            add(banner_line)
        # D1: a declarative summary/header line (e.g. budget headroom), when the
        # pivot declares a `summary` template and the provider supplied a summary.
        summary_line = self._summary_line(reg, eng._task_summary(), width)
        if summary_line is not None:
            add(summary_line)
        add(Text(""))
        if state not in ("ready", "idle") or not rows:
            return
        if reg.columns:
            # Table render. Enrich each row with the claiming worktree's title
            # (the account-scoped correlation), then render a column header, and
            # either a flat list or -- when ``group_field`` is set -- grouped
            # sections (``── repo @ account ──``). An optional dim subtitle line
            # follows each row.
            eng._enrich_pivot_rows(reg, rows)
            # D1 follow-up: fit the declared columns to `width` (the same
            # column-fit algorithm the Worktrees list uses) so a manifest whose
            # columns sum wider than the render viewport shrinks/drops columns
            # instead of silently line-wrapping the whole row.
            cols = self._fitted_columns(reg, width)
            dropped = len(reg.columns) - len(cols)
            add(self._column_header(cols, width, dropped), kind="section")
            if reg.group_field:
                groups: dict[str, list] = {}
                order: list[str] = []
                for li, rec in enumerate(rows):
                    g = str(rec.get(reg.group_field) or "· ungrouped")
                    if g not in groups:
                        groups[g] = []
                        order.append(g)
                    groups[g].append((li, rec))
                for g in order:
                    sec = Text(f"  ── {g} ", style=C_SECTION)
                    sec.append("─" * max(0, width - sec.cell_len), style=C_DIM)
                    add(sec, kind="section", new_section=g)
                    for li, rec in groups[g]:
                        add(self._column_row(cols, rec, width, sel == ("T", li),
                                              reg.worktree_field),
                            stop=("T", li), data=rec)
                        sub = self._column_subtitle(reg, rec, width)
                        if sub is not None:
                            add(sub)
                return
            for li, rec in enumerate(rows):
                add(self._column_row(cols, rec, width, sel == ("T", li),
                                      reg.worktree_field),
                    stop=("T", li), data=rec)
                sub = self._column_subtitle(reg, rec, width)
                if sub is not None:
                    add(sub)
            return
        li = 0
        for grp, entries in eng._task_groups():
            sec = Text(f"  ── {grp} ", style=C_SECTION)
            sec.append("─" * max(0, width - sec.cell_len), style=C_DIM)
            add(sec, kind="section", new_section=grp)
            for _idx, rec in entries:
                add(self._row(reg, rec, width, sel == ("T", li)),
                    stop=("T", li), data=rec)
                li += 1
