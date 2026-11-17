"""A reusable multi-select model for the Textual picker's list surfaces.

`ListSelection` wraps a set of row ids with the toggle / select-all /
group-toggle semantics shared across the picker's lists:

* **Maintenance** (`#1345`) — the checkbox column + "Select all" + per-group
  toggle,
* **Clean/Sync per-row unselect** (`#2179`) — dropping individual worktrees from
  a bucket preset, and
* — from Phase 2b of the generic-list-interaction effort (#2228) — the
  **Worktrees** list itself.

Before this, each surface open-coded the same set arithmetic
(`symmetric_difference_update`, `ids <= sel`, `sel & ids`, `sel -= ids`, ...).
Centralizing it here gives one place to reason about selection and lets a new
list surface get multi-select for free. The class is deliberately tiny and
set-like so call sites read naturally.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Set


class ListSelection:
    """A mutable set of selected row ids with picker-shaped helpers.

    Membership (``in``), truthiness, ``len()``, and iteration behave like the
    underlying set, so a :class:`ListSelection` drops into most places the old
    raw ``set`` was used. The named helpers capture the recurring intents so the
    engine stops re-deriving them.
    """

    __slots__ = ("_ids",)

    def __init__(self, ids: Iterable | None = None) -> None:
        self._ids: set = set(ids) if ids is not None else set()

    # -- set-like surface ---------------------------------------------------
    def __contains__(self, item: object) -> bool:
        return item in self._ids

    def __iter__(self) -> Iterator:
        return iter(self._ids)

    def __len__(self) -> int:
        return len(self._ids)

    def __bool__(self) -> bool:
        return bool(self._ids)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ListSelection):
            return self._ids == other._ids
        if isinstance(other, (set, frozenset)):
            return self._ids == other
        return NotImplemented

    def __repr__(self) -> str:
        return f"ListSelection({sorted(self._ids)!r})"

    @property
    def ids(self) -> set:
        """A *copy* of the selected id set (safe for the caller to mutate)."""
        return set(self._ids)

    # -- mutation -----------------------------------------------------------
    def clear(self) -> None:
        self._ids.clear()

    def replace(self, ids: Iterable) -> None:
        """Replace the whole selection with ``ids`` (e.g. select-focused-only)."""
        self._ids = set(ids)

    def toggle(self, item) -> bool:
        """Flip one id; return True if it is now selected."""
        if item in self._ids:
            self._ids.discard(item)
            return False
        self._ids.add(item)
        return True

    def toggle_all(self, ids: Iterable) -> bool:
        """Select-all / clear over ``ids``.

        If every id in ``ids`` is already selected, remove them all and return
        False (now cleared); otherwise add them all and return True (now
        selected). This is the "Select all" checkbox and per-group toggle
        behavior. An empty ``ids`` is a no-op that returns False.
        """
        ids = set(ids)
        if ids and ids <= self._ids:
            self._ids -= ids
            return False
        self._ids |= ids
        return bool(ids)

    # -- queries ------------------------------------------------------------
    def count(self, ids: Iterable) -> int:
        """How many of ``ids`` are selected (the intersection size)."""
        return len(self._ids & set(ids))

    def all_selected(self, ids: Iterable) -> bool:
        """True when ``ids`` is non-empty and *every* id is selected."""
        ids = set(ids)
        return bool(ids) and ids <= self._ids

    def difference(self, ids: Set | Iterable) -> set:
        """``ids`` with the selection removed — used to net a preset against an
        exclusion selection (Clean/Sync: ``union − excluded``)."""
        return set(ids) - self._ids


__all__ = ["ListSelection"]
