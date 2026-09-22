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
    """NF5-5 (#88): the pinned section header for the native OptionList body.

    A native ``OptionList`` scrolls all options uniformly, so a section header
    (Active / Recent / Completed, or a maintenance group) scrolls off the top --
    unlike the text-line body, which pins the current section. This 1-row widget
    sits just above the list and shows the current top section's header while
    that section's own header row is scrolled out of view; it is hidden
    (``display = False``, taking no space) at the top of the list or when the
    header itself is visible, so the unscrolled layout is unchanged."""

    can_focus = False

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._line = None
        self.display = False

    def set_line(self, line, keep_space: bool = False) -> None:
        # ``keep_space`` decouples *what to show* from *does the row exist*: while
        # the list is scrolled we keep this 1-row region present with a blank
        # line (rather than collapsing it to height 0) so a section boundary
        # never reflows the OptionList by a row. That reflow -- the pin toggling
        # ``display`` on/off as headers passed the top -- was the visible flicker
        # (#169). Only the unscrolled top fully hides it, preserving the at-rest
        # grid parity the feature was built to keep.
        cur = self._line.plain if self._line is not None else None
        new = line.plain if line is not None else None
        want_display = bool(line is not None or keep_space)
        if cur == new and self.display == want_display:
            return
        self._line = line
        self.display = want_display
        self.refresh()

    def render(self):
        return self._line if self._line is not None else Text("")

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
        # otherwise leave a query/sort-narrowed view silently stale.
        try:
            if kind == "registered":
                rows = scr._task_rows()
            elif kind == "maintenance":
                rows = scr.maint_records()
            else:
                rows = scr.list_records()
            nrows = len(rows)
            fp = tuple((r.get("id"), r.get("title"), r.get("state"),
                        r.get("age_secs"))
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
        """Pin the current section header above the list when its own header row
        has scrolled out of view (#88 NF5-5). Hidden at the top of the list or
        when the section header is itself visible, so the unscrolled layout is
        unchanged."""
        scr = self._screen
        try:
            sticky = scr.query_one("#nf-body-sticky", _PickerStickyHeader)
        except Exception:
            return
        y = int(getattr(self.scroll_offset, "y", 0) or 0)
        if y <= 0:
            # Unscrolled: fully hide the pin so the at-rest layout / grid parity
            # is unchanged (#88 NF5-5).
            sticky.set_line(None)
            return
        # Scrolled: keep the 1-row region present for EVERY offset (``keep_space``)
        # so its presence never toggles the body's height mid-scroll -- blank it
        # when there is nothing to pin (the top visible row is itself a section
        # header, or out of range) instead of collapsing it, which used to jump
        # the list by a row at each section boundary (the flicker, #169).
        if y >= len(self._sections) or (
            y < len(self._kinds) and self._kinds[y] == "section"
        ):
            sticky.set_line(None, keep_space=True)
            return
        label = self._sections[y] if y < len(self._sections) else None
        line = self._section_texts.get(label) if label else None
        sticky.set_line(line, keep_space=True)

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
        self._rebuild()

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
