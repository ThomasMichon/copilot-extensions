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


__all__ = ["ListView"]
