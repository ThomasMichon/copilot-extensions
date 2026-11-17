"""Spike/prototype: a ``ListView``-backed "v2" render of the Worktrees pivot's
data body (worktrees-pivot-ux-overhaul, Phase 2).

Standalone and purely additive: mounts its OWN small ``App``/``Screen`` and
never touches ``PickerScreen``'s real NF5 compose tree (``engine.py``) or its
capture-seam invariants (``engine_rendering.py``'s ``compose``/
``_frame_segments``). Feeds off the SAME real, fully-derived
``(cols, sections)`` data the real Worktrees pivot renders -- callers drive
the real ``PickerApp`` under ``preview.enable_preview_mode()`` and pass in
``PickerScreen.current_list_visible()``'s output, so this is an
apples-to-apples visual/interaction comparison against today's
``OptionList``-backed body (``engine_regions._PickerNativeData`` +
``engine_views.WorktreesView``), not a hand-faked mock.

Today's body renders each worktree record as TWO adjacent ``OptionList``
options (a focusable title row + a disabled detail row, stitched to look
like one row -- see ``engine_regions._PickerNativeData._rebuild``). This
spike instead composes each record as ONE real ``ListItem`` widget: a
``Checkbox`` (native, clickable, focusable) + a two-line title/detail
``Static``. Section bands ("── Active ──") become disabled, non-selectable
``ListItem``s, exactly mirroring the disabled-option convention already used
by ``OptionList`` today.

This module intentionally reuses the SAME shared column-fit/row-render
helpers every pivot already shares (``engine_helpers.fit``/``row_text``/
``header_text``) -- migrating the *container* must not fork the
column-declarative model ``pivot_manifest.py`` and every pivot (built-in and
contributed) rely on.

NOT wired into any pivot's real render path or an opt-in flag yet -- this
validates feasibility/feel first. See the effort's
``phase2-native-textual-audit.md`` for the full tradeoff writeup this spike
informs, and its own module docstring note on what a real integration would
still need (the scroll-preservation / sticky-header / incremental single-row
repaint bridge ``_PickerNativeData`` already built for ``OptionList``).
"""
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Checkbox, Footer, Header, ListItem, Static
from textual.widgets import ListView as TextualListView

from .engine_helpers import (
    C_DIM,
    C_LABEL,
    C_SECTION,
    C_STATE,
    _NO_FLEX_COLUMN,
    fit,
    header_text,
    row_text,
)


class _SectionHeaderItem(ListItem):
    """A non-selectable, full-width section banner ("── Active ──"), mirroring
    the disabled-option convention ``_PickerNativeData`` already uses for
    section headers today."""

    can_focus = False

    def __init__(self, label: str, width: int) -> None:
        sec = Text(f"  ── {label} ", style=C_SECTION)
        sec.append("─" * max(0, width - sec.cell_len), style=C_DIM)
        super().__init__(Static(sec))
        self.disabled = True


class _HeaderRowItem(ListItem):
    """The column-header row ("ID  STATE  R  AGE  LIVE  T  PR")."""

    can_focus = False

    def __init__(self, lcols, width: int) -> None:
        super().__init__(Static(header_text(lcols, width, indent=2)))
        self.disabled = True


def _detail_text(rec: dict, width: int) -> Text:
    """A simplified title/activity detail line -- title + state, truncated to
    fit. Deliberately omits the real ``_detail_line``'s pulse-animation/
    claims-badge polish (orthogonal to this spike's question: does the
    ListView CONTAINER/composition model work), to keep this module a small,
    self-contained spike rather than duplicating ``WorktreesView``'s
    ``self._eng``-coupled rendering."""
    title = str(rec.get("title") or "").strip() or "(untitled)"
    state = str(rec.get("state") or "").strip().lower()
    t = Text("  ")
    avail = max(1, width - t.cell_len - (len(state) + 2 if state else 0))
    if len(title) > avail:
        title = title[: max(0, avail - 1)] + "…" if avail > 1 else title[:avail]
    t.append(title, style=C_LABEL)
    if state:
        t.append(": ", style=C_DIM)
        t.append(state, style=C_STATE.get(rec.get("state", ""), C_DIM))
    return t


class _WorktreeItem(ListItem):
    """One worktree record: a real ``Checkbox`` + a two-line composed body
    (title line via the shared ``row_text`` column renderer, detail line via
    :func:`_detail_text`) -- ONE native list item per record, vs. today's two
    adjacent ``OptionList`` options."""

    def __init__(self, rec: dict, lcols, width: int, *, checked: bool = False) -> None:
        title_txt = row_text(rec, lcols, max(1, width - 6), False)
        detail_txt = _detail_text(rec, max(1, width - 6))
        body = Vertical(Static(title_txt), Static(detail_txt))
        super().__init__(Horizontal(Checkbox(value=checked), body))
        self.rec_id = rec.get("id4")


class ListViewPivotProto(App):
    """A standalone spike App: renders the Worktrees pivot's
    ``(cols, sections)`` data through a real Textual ``ListView`` instead of
    ``OptionList``. Constructed with already-derived data (see module
    docstring) -- never fetches or derives data itself."""

    CSS = """
    Screen { background: #1a1a1a; }
    ListView { height: 1fr; }
    ListItem { padding: 0 1; height: auto; }
    ListItem > Horizontal { height: auto; }
    ListItem > Horizontal > Checkbox { height: 1; }
    ListItem > Horizontal > Vertical { height: auto; width: 1fr; }
    ListItem > Horizontal > Vertical > Static { height: 1; }
    ListItem > Static { height: 1; }
    """

    def __init__(self, cols, sections, width: int = 112) -> None:
        super().__init__()
        self._cols = cols
        self._sections = sections
        self._width = width

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield TextualListView(id="v2-body")
        yield Footer()

    async def on_mount(self) -> None:
        lv = self.query_one("#v2-body", TextualListView)
        width = self._width
        lcols = fit(self._cols, max(1, width - 8), _NO_FLEX_COLUMN, 0)
        items = [_HeaderRowItem(lcols, width)]
        for label, rows in self._sections:
            items.append(_SectionHeaderItem(label, width))
            for rec in rows:
                items.append(_WorktreeItem(rec, lcols, width))
        await lv.extend(items)
