"""Generic filter + sort state for a picker list surface (Phase 4, #2228).

`ListView` is the client-side counterpart to `ListSelection` (`selection.py`):
a free-text filter plus a cyclable sort key, operating over the caller's own
already-fetched, already-normalized rows. No Textual/picker dependency, so it
is testable in isolation and reusable by any future list surface (the
Worktrees list first; registered pivots next, per the effort's own
sequencing) without reimplementing filter/sort per contributor.

Cross-effort record-shape contract (picker-list-interaction-layer README,
Phase 4): a filter must never silently drop a row a live-signals contract
depends on without an explicit affordance. `filter()`'s `keep` predicate is
that affordance -- a caller passes one that recognizes its own "always
show" rows (e.g. a live/bare-orphan worktree) so a query narrowing the view
can never make the row the operator is mid-session on simply vanish.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence


class ListView:
    """Free-text filter + sort-key state for one list surface.

    ``query`` is the raw command-bar text (empty = no filter). ``sort_index``
    selects a ``(label, key_fn)`` pair from the caller-supplied ``keys``
    sequence, cycled by :meth:`cycle_sort`. Both fields are plain, directly
    settable state -- the class only owns the transform logic, not a command
    mode/UI (that's the picker engine's ``_dispatch_key`` concern).
    """

    def __init__(self) -> None:
        self.query: str = ""
        self.sort_index: int = 0

    def filter(
        self,
        records: Iterable,
        fields: Sequence[str],
        keep: Callable[[object], bool] | None = None,
    ) -> list:
        """Return only ``records`` whose ``fields`` (joined, case-folded)
        contain ``query`` as a substring -- or every record, unfiltered, when
        ``query`` is empty/whitespace-only. A record for which ``keep``
        returns true is always included, regardless of match (the
        record-shape-contract escape hatch above)."""
        q = self.query.strip().casefold()
        if not q:
            return list(records)
        out = []
        for rec in records:
            if keep is not None and keep(rec):
                out.append(rec)
                continue
            hay = " ".join(str(rec.get(f, "") or "") for f in fields).casefold()
            if q in hay:
                out.append(rec)
        return out

    def sort(self, records: Iterable, keys: Sequence[tuple[str, Callable]]) -> list:
        """Stably sort ``records`` by the currently-selected ``(label,
        key_fn)`` pair in ``keys``; an empty ``keys`` returns ``records``
        unchanged (the caller's own default order, e.g. `derive.bucket`'s
        per-section age order, stands)."""
        if not keys:
            return list(records)
        _label, key_fn = keys[self.sort_index % len(keys)]
        return sorted(records, key=key_fn)

    def cycle_sort(self, keys: Sequence[tuple[str, Callable]]) -> None:
        """Advance to the next sort key in ``keys`` (wraps around); a no-op
        when ``keys`` is empty."""
        if keys:
            self.sort_index = (self.sort_index + 1) % len(keys)

    def sort_label(self, keys: Sequence[tuple[str, Callable]]) -> str | None:
        """The active sort key's display label, or ``None`` with no keys."""
        if not keys:
            return None
        return keys[self.sort_index % len(keys)][0]

    def clear(self) -> None:
        """Reset the filter text (Esc's first press, #2228 Phase 4) -- leaves
        the sort selection alone; only ``/`` narrowing is a "back out" step."""
        self.query = ""

    def narrow(self, rows, fields, keep, keys) -> list:
        """Filter then sort ``rows`` by the current query/sort state."""
        return self.sort(self.filter(rows, fields, keep=keep), keys)

    def handle_compose_key(self, key: str, character: str | None = None) -> str:
        """Compose one keystroke into ``query`` while the "/" command bar is
        active (moved out of the picker engine to keep it under its own
        module-size budget, #2228 Phase 4 review). Returns ``"commit"``
        (Enter: leave compose mode, keep the query), ``"cancel"`` (Escape:
        leave compose mode, clear the query), or ``"continue"`` (still
        composing). Prefers ``character`` over ``key`` so a NAMED printable
        key token (e.g. Textual's ``"slash"`` for ``/``) still lands in the
        query instead of being dropped; Space is named too."""
        if key == "enter":
            return "commit"
        if key == "escape":
            self.clear()
            return "cancel"
        if key == "backspace":
            self.query = self.query[:-1]
            return "continue"
        if key == "space":
            self.query += " "
            return "continue"
        ch = character if (character and len(character) == 1
                            and character.isprintable()) else key
        if len(ch) == 1 and ch.isprintable():
            self.query += ch
        return "continue"


def resolve_index(key, old_idx, ids):
    """Resolve a captured row ``key`` to its new position in ``ids``, or --
    when that row is gone entirely (filtered/removed, not just moved) -- the
    equivalent index clamped into ``ids`` (Phase 3's "focus stays at the
    equivalent index" rule); ``None`` only when ``ids`` is now empty.
    Shared by every picker index (focus/anchor/remembered row) that must
    survive a reorder or re-filter, #2228 Phase 4 review."""
    if key is not None and key in ids:
        return ids.index(key)
    if not ids:
        return None
    return min(old_idx, len(ids) - 1) if old_idx is not None else 0


def capture_row_refs(ids, sel, wt_anchor, last_l):
    """Snapshot focus/anchor/last_l by stable key + old index, for
    :func:`remap_row_refs` after a reorder/re-filter (#2228 Phase 4)."""
    def key_at(i):
        return ids[i] if i is not None and 0 <= i < len(ids) else None

    focus_idx = sel[1] if sel[0] == "L" else None
    return {
        "focus_key": key_at(focus_idx), "focus_idx": focus_idx,
        "anchor_key": key_at(wt_anchor), "anchor_idx": wt_anchor,
        "last_l_key": key_at(last_l), "last_l_idx": last_l,
    }


def remap_row_refs(refs, ids):
    """Resolve a :func:`capture_row_refs` snapshot against the new ``ids``.
    Returns ``(last_l, focus_idx_or_None, anchor)`` via :func:`resolve_index`;
    the caller applies picker-specific fallbacks (default_sel(), stops())."""
    last_l = resolve_index(refs["last_l_key"], refs["last_l_idx"], ids)
    focus = None
    if refs["focus_idx"] is not None:
        focus = resolve_index(refs["focus_key"], refs["focus_idx"], ids)
    anchor = resolve_index(refs["anchor_key"], refs["anchor_idx"], ids)
    return last_l, focus, anchor


def render_command_bar(list_view, composing, width, c_dim, keys):
    """The "/" command-bar chrome row: filter text (with a cursor while
    composing) + the active sort label, once cycled off its default."""
    from rich.text import Text

    t = Text("  / " + list_view.query, style="" if composing else c_dim)
    if composing:
        t.append("▏")
    sort_lbl = list_view.sort_label(keys)
    if sort_lbl and list_view.sort_index:
        suffix = f"  sort: {sort_lbl}"
        if t.cell_len + len(suffix) <= width:
            t.append(suffix, style=c_dim)
    if t.cell_len < width:
        t.append(" " * (width - t.cell_len))
    return t


__all__ = ["ListView", "resolve_index", "capture_row_refs", "remap_row_refs",
           "render_command_bar"]
