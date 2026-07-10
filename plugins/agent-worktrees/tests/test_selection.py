"""Unit tests for the reusable ListSelection multi-select model (#2228 Phase 2a)."""

from __future__ import annotations

from agent_worktrees.picker_tui.selection import ListSelection


def test_empty_selection_is_falsey_and_zero_len():
    sel = ListSelection()
    assert not sel
    assert len(sel) == 0
    assert list(sel) == []
    assert "a" not in sel


def test_init_from_iterable():
    sel = ListSelection(["a", "b", "a"])
    assert len(sel) == 2
    assert "a" in sel and "b" in sel
    assert sel.ids == {"a", "b"}


def test_toggle_adds_then_removes_and_reports_state():
    sel = ListSelection()
    assert sel.toggle("a") is True          # now selected
    assert "a" in sel
    assert sel.toggle("a") is False         # now deselected
    assert "a" not in sel


def test_toggle_all_selects_when_not_all_present():
    sel = ListSelection(["a"])
    assert sel.toggle_all({"a", "b", "c"}) is True    # not all present -> select all
    assert sel.ids == {"a", "b", "c"}


def test_toggle_all_clears_when_all_present():
    sel = ListSelection(["a", "b", "c", "z"])
    assert sel.toggle_all({"a", "b", "c"}) is False   # all present -> drop them
    assert sel.ids == {"z"}                            # unrelated id untouched


def test_toggle_all_empty_is_noop():
    sel = ListSelection(["a"])
    assert sel.toggle_all(set()) is False
    assert sel.ids == {"a"}


def test_count_is_intersection_size():
    sel = ListSelection(["a", "b", "c"])
    assert sel.count({"a", "c", "x"}) == 2
    assert sel.count(set()) == 0


def test_all_selected_requires_nonempty_and_subset():
    sel = ListSelection(["a", "b"])
    assert sel.all_selected({"a", "b"}) is True
    assert sel.all_selected({"a"}) is True
    assert sel.all_selected({"a", "b", "c"}) is False
    assert sel.all_selected(set()) is False           # empty is never "all selected"


def test_replace_and_clear():
    sel = ListSelection(["a", "b"])
    sel.replace({"x"})
    assert sel.ids == {"x"}
    sel.clear()
    assert not sel and sel.ids == set()


def test_difference_nets_a_preset_against_the_selection():
    excluded = ListSelection(["b"])
    # union - excluded  (Clean/Sync net set)
    assert excluded.difference({"a", "b", "c"}) == {"a", "c"}


def test_ids_returns_a_defensive_copy():
    sel = ListSelection(["a"])
    got = sel.ids
    got.add("b")
    assert "b" not in sel                              # mutating the copy is safe


def test_equality_with_set_and_other_selection():
    assert ListSelection(["a", "b"]) == {"a", "b"}
    assert ListSelection(["a"]) == ListSelection(["a"])
    assert ListSelection(["a"]) != ListSelection(["b"])
