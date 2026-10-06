#!/usr/bin/env python3
"""Native Textual region widgets extracted from ``engine.py``."""
from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widget import Widget
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from .engine_helpers import (
    _NO_FLEX_COLUMN,
    fit,
    row_sess_pulses,
)

if TYPE_CHECKING:
    from .engine import PickerScreen

class _PickerSegment(Widget):
    """One NF2 screen segment (or a row-slice of one) -- a leaf widget that
    renders its slice of ``PickerScreen._frame_segments()`` (#88 NF2/NF3).

    The screen owns all state and content; a segment is a pure view that reads
    its named slice back off the parent screen at render time. An optional
    ``rows`` selects a sub-range of the named segment's rows (used to split the
    header into its ``title`` and ``pivots`` region rows, #88 NF3). Not
    focusable -- the compose tree is the *visual* skeleton only; focus/keys stay
    on the manual ``sel``/``on_key`` model until the later NF slices migrate
    regions and rows to real focusable widgets.
    """

    can_focus = False

    def __init__(self, screen: PickerScreen, seg_key: str,
                 rows: slice | None = None, **kw) -> None:
        super().__init__(**kw)
        self._screen = screen
        self._seg_key = seg_key
        self._rows = rows

    def render(self):
        seg = self._screen._frame_segments()
        lines = seg[self._seg_key]
        if self._rows is not None:
            lines = lines[self._rows]
        return self._screen._join_lines(lines, seg["W"])

class _FocusRegion(Widget):
    """Base for a focusable chrome/data region in the NF3 compose tree (#88).

    A region widget holds native framework focus and mirrors the picker's
    manual ``sel`` model, bridging native Tab-between-regions with the
    manual dispatcher, which still owns actual navigation (retired at NF5):

    * ``on_focus`` -- framework put focus here (Tab/click): point ``sel``
      at this region's head (unless already in this region).
    * ``on_key`` -- forward every key to ``_dispatch_key`` (single source
      of truth), then mirror focus back onto whatever region ``sel`` now
      names and repaint. Consuming the key suppresses Textual's own Tab
      so region movement runs through ``region_heads`` exactly as before.

    Subclasses set ``_zone`` (the ``sel`` zone this region represents) and
    implement ``render``.
    """

    can_focus = True
    _zone: str = ""

    def __init__(self, screen: PickerScreen, **kw) -> None:
        super().__init__(**kw)
        self._screen = screen

    def on_focus(self) -> None:
        scr = self._screen
        if scr._nf_syncing or not scr._nf_mounted:
            return
        # Only move sel if it isn't already within this region (so a focus that
        # merely mirrors sel doesn't stomp the in-region index).
        if scr._widget_for_zone(scr.sel[0]) != (self.id or ""):
            scr.sel = scr.region_head(self._zone)
            scr.refresh()

    def on_key(self, event) -> None:
        scr = self._screen
        key = event.key
        # Global pivot/machine shortcuts stay owned by the picker's BINDINGS: let
        # them bubble (do NOT stop) so the action_* fires, exactly as
        # PickerScreen.on_key does. Everything else runs through the manual
        # dispatcher, then we mirror focus back onto whatever region sel names.
        # Composing (#2228 Phase 4) owns EVERY key -- checked first.
        if not scr.cmd_mode and key in scr.BINDING_KEYS:
            return
        event.stop()
        event.prevent_default()
        if event.character in ("[", "]"):
            key = event.character
        scr._dispatch_key(key, event.character)
        scr._sync_focus_to_sel()
        scr.refresh()

    def on_click(self, event) -> None:
        # Pointer parity (#88 NF4): clicking a region focuses it (Textual does
        # this for a focusable widget; on_focus then points sel at the region
        # head). Subclasses that address a finer target (a data row) override.
        event.stop()

class _PickerPivots(_FocusRegion):
    """The WORKTREES/Tasks/Bridges pivot-tabs row + ⚙ Configuration entry, as a
    focusable region (zone ``V``) (#88 NF3)."""

    _zone = "V"

    def render(self):
        scr = self._screen
        seg = scr._frame_segments()
        return scr._join_lines(seg["header"][1:2], seg["W"])

class _PickerMachine(_FocusRegion):
    """The machine-scope (``All / <machine> <env>``) row, as a focusable region
    (zone ``M``) (#88 NF3). Height auto -- it's the leading chrome rows up to the
    button region."""

    _zone = "M"

    def render(self):
        scr = self._screen
        W = scr.size.width or 100
        chrome = scr._build_chrome_vrows(W)
        machine, _buttons = scr._chrome_split(chrome)
        return scr._join_lines([vr.text for vr in machine], W)

class _PickerButtons(_FocusRegion):
    """The New/Clean/Sync (or Cleanup/Sync) button row, as a focusable region
    (zone ``BTN``) (#88 NF3). Empty (and effectively skipped) for pivots without
    a top button region, e.g. Tasks."""

    _zone = "BTN"

    def render(self):
        scr = self._screen
        W = scr.size.width or 100
        chrome = scr._build_chrome_vrows(W)
        _machine, buttons = scr._chrome_split(chrome)
        return scr._join_lines([vr.text for vr in buttons], W)

class _PickerStickyHeader(Widget):
    """NF5-5 (#88) + Phase 9 item 1 (worktrees-pivot-ux-overhaul #3307): the
    pinned column-header + current-group-band region above the native
    OptionList body.

    A native ``OptionList`` scrolls all options uniformly, so both the
    column-header row (``ID STATE AGE ...``) and a section header (Active /
    Recent / Completed, or a maintenance group) scroll off the top -- unlike
    the text-line body, which pinned the current section only. This 2-row
    widget sits just above the list: row 0 pins the column header, row 1 pins
    the current top section's header, each shown only while its own source
    row has scrolled out of view. Both rows are hidden together
    (``display = False``, taking no space) at the top of the list; once
    scrolled, BOTH rows stay reserved (blank when nothing applies to that
    slot) so a section/column-header boundary never reflows the OptionList by
    a row -- the same anti-flicker contract the original 1-row section pin
    established (#169), now generalized to two independently-blankable rows."""

    can_focus = False

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._colhdr_line = None
        self._section_line = None
        self.display = False

    def set_lines(self, colhdr_line, section_line,
                  keep_space: bool = False) -> None:
        # ``keep_space`` decouples *what to show* from *does the region exist*:
        # see the class docstring -- while scrolled, this 2-row region stays
        # present (blank rows where nothing applies) rather than collapsing,
        # so pin toggling never reflows the OptionList (#169).
        cur = (self._colhdr_line.plain if self._colhdr_line is not None else None,
               self._section_line.plain if self._section_line is not None else None)
        new = (colhdr_line.plain if colhdr_line is not None else None,
               section_line.plain if section_line is not None else None)
        want_display = bool(
            colhdr_line is not None or section_line is not None or keep_space)
        if cur == new and self.display == want_display:
            return
        self._colhdr_line = colhdr_line
        self._section_line = section_line
        self.display = want_display
        self.refresh()

    def render(self):
        from rich.console import Group
        top = self._colhdr_line if self._colhdr_line is not None else Text("")
        bot = self._section_line if self._section_line is not None else Text("")
        return Group(top, bot)

class _PickerNativeData(OptionList):
    """NF5-5 (#88): swappable *native* data body -- the pivot's data rows as
    real ``OptionList`` options (native focus, cursor, up/down, click, scroll,
    a11y) instead of painted text lines driven by the manual ``sel`` cursor.

    Bridged to the engine ``sel`` model both ways so the rest of the picker
    (chrome, activation, tests) is unchanged: native cursor moves mirror into
    ``sel``, and external ``sel`` changes mirror onto the native highlight.
    Header/live-pulse rows are *disabled* options, like the manual ``stops``
    list; options rebuild only when the data signature changes.
    """

    can_focus = True
    _SENTINEL_SEL = ("__native__", -1)
    #: Keys OptionList owns natively; everything else routes through the manual
    #: model so Tab/pivot/machine behave as elsewhere.
    _NATIVE_KEYS = frozenset({"up", "down", "home", "end", "pageup", "pagedown",
                              "enter"})

    def __init__(self, screen, **kw) -> None:
        super().__init__(**kw)
        self._screen = screen
        self._stops = []          # option index -> sel stop (None = header row)
        self._sections = []       # option index -> section label active there
        self._section_texts = {}  # section label -> its header Text (for sticky)
        self._kinds = []          # option index -> VRow kind ('section' etc.)
        self._l_rows = {}         # worktree row key -> (option index, rec, li)
        self._colhdr_index = None  # option index of the pivot's column header
        self._colhdr_text = None   # its Text (for sticky), or None (no colhdr)
        self._sig = None          # last data signature (rebuild only on change)
        self._syncing = False
        self._suppress_activate = False   # one-shot: gutter click toggled, don't open

    # ---- data <-> options -------------------------------------------------
    def _signature(self):
        scr = self._screen
        wt = tuple(sorted(scr.wt_sel.ids)) if hasattr(scr, "wt_sel") else ()
        kind = scr._kind()
        # A cheap per-pivot data-size probe so the options rebuild when the
        # active pivot's rows change (tasks load, maintenance candidates), not
        # only when the worktrees list does. ``fp`` (review finding, #2228 P4)
        # additionally fingerprints every sort-dependent field (id/title/state/
        # age_secs) -- nrows alone cannot see a same-cardinality reload that
        # swaps a row's content (title/state/age change), which would
        # otherwise leave a query/sort-narrowed view silently stale. ``sess``
        # (render-perf follow-up, #3307 2026-09-30) closes a real staleness
        # bug: the Group C mux reconcile lands asynchronously AFTER first
        # paint and can flip a row's LIVE value (e.g. ``PROC`` -> ``MUX(1)``)
        # with its id/title/state/age_secs all unchanged (mux attachment
        # doesn't affect ``state``, which already collapsed to ACTIVE via the
        # cache-only bound/lock-live hint) -- so without ``sess`` in the
        # fingerprint, that correction never triggered a rebuild and the row
        # stayed showing its stale first-paint glyph indefinitely.
        try:
            if kind == "registered":
                rows = scr._task_rows()
            elif kind == "maintenance":
                rows = scr.maint_records()
            else:
                rows = scr.list_records()
            nrows = len(rows)
            fp = tuple((r.get("id"), r.get("title"), r.get("state"),
                        r.get("age_secs"), r.get("sess"))
                       for r in rows)
        except Exception:
            nrows, fp = -1, ()
        return (kind, scr.htab, scr.machine_idx, nrows, wt,
                getattr(scr, "pulse", 0), getattr(scr, "update_state", None),
                getattr(scr, "manager_update_state", None),
                scr.size.width, getattr(scr, "cmd_mode", False),
                getattr(scr.list_view, "query", ""),
                getattr(scr.list_view, "sort_index", 0), fp)

    def _rebuild(self):
        scr = self._screen
        W = scr.size.width or 100
        # Data-only build (chrome renders separately); resilient to an early mount.
        try:
            data = scr._build_data_vrows(W, sel=self._SENTINEL_SEL)
        except Exception:
            data = []
        # `clear_options()` zeroes scroll_y; restore across a same-pivot rebuild.
        new_sig = self._signature()
        preserve_scroll = self._sig is not None and self._sig[:3] == new_sig[:3]
        old_scroll_y = int(getattr(self.scroll_offset, "y", 0) or 0)
        self._syncing = True
        try:
            self.clear_options()
            self._stops = []
            self._sections = []
            self._kinds = []
            self._l_rows = {}
            self._section_texts = {}
            self._colhdr_index = None
            self._colhdr_text = None
            cur_label = None
            opts = []
            for vr in data:
                stop = getattr(vr, "stop", None)
                text = vr.text if isinstance(vr.text, Text) else Text(str(vr.text))
                kind = getattr(vr, "kind", None)
                ps = getattr(vr, "pin_section", None)
                cur_label = ps[0] if ps else cur_label
                if kind == "section" and cur_label is not None:
                    self._section_texts[cur_label] = text
                if kind == "colhdr" and self._colhdr_index is None:
                    # Exactly one column-header row per pivot (Phase 9 item 1):
                    # pin it independently of the current-section band.
                    self._colhdr_index = len(opts)
                    self._colhdr_text = text
                idx = len(opts)
                opts.append(Option(text, disabled=stop is None))
                self._stops.append(stop)
                self._sections.append(cur_label)
                self._kinds.append(kind)
                # Row key for the #171 selection-only repaint (skip a full rebuild).
                rec = getattr(vr, "data", None)
                if stop is not None and stop[0] == "L" and rec is not None:
                    rid = scr._row_key(rec)
                    if rid is not None:
                        self._l_rows[rid] = (idx, rec, stop[1])
            if opts:
                self.add_options(opts)
            if preserve_scroll and old_scroll_y:
                self.scroll_y = old_scroll_y   # clamped by the reactive's own validator
        finally:
            self._syncing = False
        self._sig = new_sig
        self._sync_from_sel(quiet=preserve_scroll)
        self._update_sticky()

    def _index_for_stop(self, stop):
        for i, s in enumerate(self._stops):
            if s == stop:
                return i
        return None

    def _first_enabled_index(self):
        """The option index of the list's topmost focusable row (the first
        non-``None`` stop) -- used to force a full scroll-to-top when focus
        lands there (Phase 9 item 1, #3307), since it is usually several rows
        below option index 0 (the column header / section band precede it)."""
        return next((i for i, s in enumerate(self._stops) if s is not None),
                    None)

    def _sync_from_sel(self, *, quiet: bool = False):
        """Point the native cursor at the option owning the engine's ``sel``.

        ``quiet`` re-applies ``highlighted`` (via ``set_reactive``, bypassing
        the watcher) without invoking Textual's own ``scroll_to_highlight``
        side effect. Used by :meth:`_rebuild` when a same-pivot rebuild
        (e.g. a periodic live-pulse tick) already restored the operator's own
        scroll position: ``clear_options()`` unconditionally resets
        ``highlighted`` to ``None``, so re-establishing it here would
        otherwise look like a genuine cursor move and immediately snap the
        list back to the cursor -- discarding a mouse-wheel scroll the
        operator made while their focus row hadn't changed. A REAL focus
        move (arrow keys/click changing ``sel``, or a genuine pivot switch)
        still goes through the normal noisy path and scrolls into view
        immediately, per the "scroll moves on focus change, not after" rule.
        """
        idx = self._index_for_stop(self._screen.sel)
        if idx is None or self.highlighted == idx:
            return
        self._syncing = True
        try:
            if quiet:
                self.set_reactive(OptionList.highlighted, idx)
            else:
                self.highlighted = idx
        finally:
            self._syncing = False

    def _update_sticky(self):
        """Pin the column header + current section header above the list when
        their own source rows have scrolled out of view (#88 NF5-5; Phase 9
        item 1, #3307). Hidden at the top of the list when NEITHER needs
        pinning, so the unscrolled layout is unchanged."""
        scr = self._screen
        try:
            sticky = scr.query_one("#nf-body-sticky", _PickerStickyHeader)
        except Exception:
            return
        y = int(getattr(self.scroll_offset, "y", 0) or 0)
        if y <= 0:
            # Unscrolled: fully hide the pin so the at-rest layout / grid parity
            # is unchanged (#88 NF5-5).
            sticky.set_lines(None, None)
            return
        # Scrolled: keep the 2-row region present for EVERY offset (``keep_space``)
        # so its presence never toggles the body's height mid-scroll -- blank a
        # slot when there is nothing to pin in it (the column header/top visible
        # row is itself that same row, or out of range) instead of collapsing it,
        # which used to jump the list by a row at each boundary (the flicker,
        # #169).
        colhdr_line = None
        if (self._colhdr_index is not None and y > self._colhdr_index):
            colhdr_line = self._colhdr_text
        section_line = None
        if not (y >= len(self._sections) or (
                y < len(self._kinds) and self._kinds[y] == "section")):
            label = self._sections[y] if y < len(self._sections) else None
            section_line = self._section_texts.get(label) if label else None
        sticky.set_lines(colhdr_line, section_line, keep_space=True)

    def refresh_data(self):
        """Called by the screen on a state change (in place of a plain
        ``refresh()``): rebuild the options only when the data signature changed
        -- so plain cursor moves stay smooth and native -- else just mirror
        ``sel`` onto the native highlight. Always re-pins the sticky header
        (cheap) so a mouse-wheel scroll (no highlight change) tracks too."""
        new_sig = self._signature()
        if new_sig == self._sig:
            self._sync_from_sel()
            self._update_sticky()
            return
        # Held-arrow fast path (#171): when only the focus-tracking selection
        # moved (same pivot + data, only the wt_sel field changed), repaint just
        # the rows whose checkbox glyph flipped -- O(delta), not a full O(rows)
        # clear+rebuild -- so a held up/down arrow keeps up with key-repeat.
        # Safe against the old line-wrap jitter because the native scrollbar is
        # hidden (see the CSS): content width is constant, so a replaced row can
        # never be re-measured at a narrower width and wrap.
        if self._try_selection_repaint(new_sig):
            self._sig = new_sig
            self._sync_from_sel()
            self._update_sticky()
            return
        # Pulse-only fast path (render-perf follow-up, #3307 2026-09-30): the
        # 10fps idle tick flips ``pulse`` roughly twice a second purely to
        # animate a live-session glyph's color -- with no other state change,
        # that used to force this ENTIRE list (every row, every pivot) to
        # clear and rebuild from scratch on every flip, even though at most a
        # handful of rows (the genuinely live ones, see ``row_sess_pulses``)
        # actually render any differently. Repaint just those rows in place,
        # same as the selection fast path above.
        if self._try_pulse_repaint(new_sig):
            self._sig = new_sig
            self._sync_from_sel()
            self._update_sticky()
            return
        # Content-only fast path (pivot-streaming-transport Phase 4): a
        # background reconcile/poll (e.g. a PR state or git-status refresh,
        # `_maybe_repoll()`) that updates one or a few rows' title/age/sess
        # fields -- with the same row count, same order, and no row CHANGING
        # STATE (a state change can move a row to a different section, which
        # a text-only patch can't express, see `_rebuild()`'s own
        # section-grouping construction) -- used to force the exact same
        # full O(rows) clear+rebuild the pulse-only fast path was built to
        # avoid for a different trigger. Repaint just the changed rows in
        # place, same pattern as the two fast paths above.
        if self._try_content_repaint(new_sig):
            self._sig = new_sig
            self._sync_from_sel()
            self._update_sticky()
            return
        self._rebuild()

    def _try_content_repaint(self, new_sig) -> bool:
        """Repaint only the worktree rows whose own content (title/age_secs/
        sess) changed, in place. Returns True if it fully handled the
        update; False to fall back to :meth:`_rebuild`.

        Applies only when the signature delta is *content-only*: the pivot is
        Worktrees, every field except ``fp`` (index 12) is unchanged, the row
        COUNT and ORDER (the ``fp`` id sequence) are unchanged, and -- the
        safety condition that matters most -- no differing row's ``state``
        field itself changed. A state change can move a row into a different
        section (Active/Recent/Completed), which shifts the index of every
        row after it in the rebuilt option list; this fast path can only ever
        be correct for a pure content edit that leaves every row exactly
        where it already was."""
        old = self._sig
        if old is None or len(old) != len(new_sig):
            return False
        if old[0] != "worktrees" or new_sig[0] != "worktrees":
            return False
        # Differ ONLY in the fp field -- index 12 of _signature().
        if any(old[i] != new_sig[i] for i in range(len(old)) if i != 12):
            return False
        old_fp, new_fp = old[12], new_sig[12]
        if len(old_fp) != len(new_fp):
            return False
        # Row order (the id sequence) must be unchanged -- a reorder, even
        # with identical membership, can't be expressed as an in-place patch.
        if tuple(t[0] for t in old_fp) != tuple(t[0] for t in new_fp):
            return False
        changed = [i for i in range(len(old_fp)) if old_fp[i] != new_fp[i]]
        if not changed:
            return False
        # No differing row may have changed STATE (fp index 2 within each
        # per-row tuple) -- see the docstring above.
        if any(old_fp[i][2] != new_fp[i][2] for i in changed):
            return False
        scr = self._screen
        W = scr.size.width or 100
        try:
            cols, _sections = scr.current_list()
            lcols = fit(cols, W - 2, _NO_FLEX_COLUMN, 0)
            rows = scr.list_records()
        except Exception:
            return False
        if len(rows) != len(new_fp):
            return False   # list_records() raced ahead of the signature probe
        # Defensive re-check: the fresh `rows` fetch above is a SEPARATE call
        # from the one `_signature()` made to build `new_fp` -- re-derive the
        # same fingerprint from `rows` and confirm it still matches `new_fp`
        # exactly before trusting positional indices into it. A mismatch here
        # (data mutated between the two calls) falls back to a full rebuild
        # rather than risk patching the wrong row's text into the wrong slot.
        refetched_fp = tuple((r.get("id"), r.get("title"), r.get("state"),
                              r.get("age_secs"), r.get("sess")) for r in rows)
        if refetched_fp != new_fp:
            return False
        view = scr.worktrees_view
        count = self.option_count
        for i in changed:
            rec = rows[i]
            rid = scr._row_key(rec)
            if rid is None:
                return False
            row = self._l_rows.get(rid)
            if row is None:
                return False   # unknown row -> safe fallback to a full rebuild
            idx, _old_rec, li = row
            if not (0 <= idx < count):
                return False
            text = view._row_text(rec, li, self._SENTINEL_SEL, W, lcols,
                                  None, None)
            self.replace_option_prompt_at_index(idx, text)
            # Keep _l_rows current so a LATER pulse/selection fast path (or
            # another content repaint) diffs against this row's fresh data,
            # not the stale record this patch just replaced on screen.
            self._l_rows[rid] = (idx, rec, li)
        return True

    def _try_selection_repaint(self, new_sig) -> bool:
        """Repaint only the worktree rows whose checkbox glyph changed, in place
        (#171). Returns True if it fully handled the update; False to fall back
        to :meth:`_rebuild`.

        Applies only when the signature delta is *selection-only*: the pivot is
        Worktrees and ``new_sig`` differs from the last signature in nothing but
        the ``wt`` (selection ids) field -- i.e. plain/shift arrow navigation,
        where single-select tracks focus. The native list is built with a
        sentinel sel (focus is the native amber cursor, not baked into row text),
        so the only per-row change is the box glyph on the rows entering/leaving
        the selection; re-rendering just those via the SAME per-row renderer as
        the full rebuild keeps the output byte-identical."""
        old = self._sig
        if old is None or len(old) != len(new_sig):
            return False
        if old[0] != "worktrees" or new_sig[0] != "worktrees":
            return False
        # Differ ONLY in the wt (selection) field -- index 4 of _signature().
        if any(old[i] != new_sig[i] for i in range(len(old)) if i != 4):
            return False
        changed = set(old[4]) ^ set(new_sig[4])
        if not changed:
            return False
        scr = self._screen
        W = scr.size.width or 100
        try:
            cols, _sections = scr.current_list()
            lcols = fit(cols, W - 2, _NO_FLEX_COLUMN, 0)
        except Exception:
            return False
        view = scr.worktrees_view
        count = self.option_count
        for rid in changed:
            row = self._l_rows.get(rid)
            if row is None:
                return False   # unknown row -> safe fallback to a full rebuild
            idx, rec, li = row
            if not (0 <= idx < count):
                return False
            text = view._row_text(rec, li, self._SENTINEL_SEL, W, lcols,
                                  None, None)
            self.replace_option_prompt_at_index(idx, text)
        return True

    def _try_pulse_repaint(self, new_sig) -> bool:
        """Repaint only the worktree rows whose LIVE glyph actually pulses, in
        place (render-perf follow-up, #3307 2026-09-30). Returns True if it
        fully handled the update; False to fall back to :meth:`_rebuild`.

        Applies only when the signature delta is *pulse-only*: the pivot is
        Worktrees and ``new_sig`` differs from the last signature in nothing
        but the ``pulse`` field (index 5 of :meth:`_signature`) -- i.e. the
        ~10fps idle tick's cosmetic frame flip, with no real reload/nav/scope
        change alongside it. Most rows never call ``row_sess_pulses`` true (a
        finalized/unattached/non-live worktree's glyph never changes color),
        so this is typically a no-op scan rather than a full O(rows)
        clear+rebuild -- the exact over-painting the operator flagged."""
        old = self._sig
        if old is None or len(old) != len(new_sig):
            return False
        if old[0] != "worktrees" or new_sig[0] != "worktrees":
            return False
        # Differ ONLY in the pulse field -- index 5 of _signature().
        if any(old[i] != new_sig[i] for i in range(len(old)) if i != 5):
            return False
        if old[5] == new_sig[5]:
            return False
        scr = self._screen
        W = scr.size.width or 100
        try:
            cols, _sections = scr.current_list()
            lcols = fit(cols, W - 2, _NO_FLEX_COLUMN, 0)
        except Exception:
            return False
        view = scr.worktrees_view
        count = self.option_count
        for idx, rec, li in self._l_rows.values():
            if not row_sess_pulses(rec.get("sess", "")):
                continue
            if not (0 <= idx < count):
                return False
            text = view._row_text(rec, li, self._SENTINEL_SEL, W, lcols,
                                  None, None)
            self.replace_option_prompt_at_index(idx, text)
        return True

    def on_mount(self) -> None:
        self._rebuild()

    def on_resize(self, event) -> None:
        # Rebuild at the real width once layout assigns it (the first rebuild may
        # run at the size fallback before the screen is sized) -- keeps the
        # full-width section rules in parity with the text-line body.
        self.refresh_data()

    # ---- native events -> engine model -----------------------------------
    def on_focus(self) -> None:
        scr = self._screen
        if scr._nf_syncing or not scr._nf_mounted:
            return
        if scr._widget_for_zone(scr.sel[0]) != "nf-body-data":
            # Land on the first data row (not the button default_sel), so
            # focusing the list selects a row.
            head = next((s for s in self._stops if s is not None), None)
            scr.sel = head if head is not None else scr.default_sel()
            scr.refresh()
        self._sync_from_sel()

    def on_option_list_option_highlighted(self, event) -> None:
        if self._syncing:
            return
        scr = self._screen
        # Only honor a highlight change when this list actually holds focus -- a
        # highlight re-emitted while a modal is up or focus is elsewhere (e.g.
        # right after a programmatic ``sel`` set) must not clobber ``sel``.
        if scr.app.focused is not self:
            self._update_sticky()
            return
        i = event.option_index
        stop = self._stops[i] if 0 <= i < len(self._stops) else None
        if stop is not None and scr.sel != stop:
            scr.sel = stop
            scr._wt_track_focus()
            # Defer the sel-dependent CHROME refresh to the render tick instead
            # of a synchronous per-keystroke full-screen re-composite (the
            # held-arrow freeze): the native cursor already moved, so mark the
            # chrome dirty and let ``_tick`` coalesce the refresh at ~10fps.
            scr._nav_dirty = True
        if i == self._first_enabled_index():
            # Phase 9 item 1 (#3307): focusing the list's topmost row must
            # force-scroll the viewport all the way to the top. Textual's own
            # ``scroll_to_highlight()`` (already run by the ``highlighted``
            # watcher just before this event fires) only scrolls the MINIMUM
            # distance needed to bring that one row into view -- which can
            # leave the pinned column-header/section rows above it still
            # scrolled out, so previously only a mouse-wheel scroll reached
            # far enough to bring the header back.
            self.scroll_home(animate=False, immediate=True)
        self._update_sticky()

    def on_option_list_option_selected(self, event) -> None:
        # Enter / click on a row -> activate (open its submenu etc.), the native
        # parallel to the manual Enter path -- unless the press was a checkbox
        # gutter toggle (mouse multi-select), which suppresses this one open.
        if self._suppress_activate:
            self._suppress_activate = False
            return
        self._screen._activate()

    async def _on_mouse_down(self, event) -> None:
        # Mouse multi-select (#88 NF5-5): a press in the checkbox gutter (the
        # first two cells) toggles that row's multi-select; the resulting
        # OptionList selection is then suppressed (see above) so the row does not
        # also activate. A press anywhere else is a normal click-to-activate.
        self._suppress_activate = False
        scr = self._screen
        if getattr(event, "x", 99) <= 1 and scr._kind() == "worktrees":
            y = (int(getattr(self.scroll_offset, "y", 0) or 0)
                 + int(getattr(event, "y", 0)))
            stop = self._stops[y] if 0 <= y < len(self._stops) else None
            if stop and stop[0] == "L":
                ids = scr._l_ids()
                li = stop[1]
                if 0 <= li < len(ids):
                    scr.wt_sel.toggle(ids[li])
                    scr.sel = stop
                    self._suppress_activate = True
                    scr.refresh()
        await super()._on_mouse_down(event)

    def on_key(self, event) -> None:
        scr = self._screen
        key = event.key
        # Global pivot/machine shortcuts + OptionList's own native bindings
        # stay owned as usual -- except while composing (#2228 Phase 4),
        # which owns EVERY key first (Enter would otherwise activate a row
        # instead of committing the filter).
        if not scr.cmd_mode and key in scr.BINDING_KEYS:
            return
        if not scr.cmd_mode and key in self._NATIVE_KEYS:
            return
        # Everything else (Tab region cycle, [ ] pivots, ←/→, etc.) routes
        # through the manual model, then focus is mirrored back onto whatever
        # region sel names -- exactly like the text-line region bridge.
        event.stop()
        event.prevent_default()
        if event.character in ("[", "]"):
            key = event.character
        scr._dispatch_key(key, event.character)
        scr._sync_focus_to_sel()
        scr.refresh()
