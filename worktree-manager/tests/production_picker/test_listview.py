"""Unit tests for the reusable ListView filter/sort model (#2228 Phase 4)."""

from __future__ import annotations

from worktree_manager.production_picker.picker_tui.listview import ListView


def _rec(**kw):
    d = {"title": "", "id": ""}
    d.update(kw)
    return d


def test_empty_query_returns_every_record_unfiltered():
    lv = ListView()
    recs = [_rec(title="a"), _rec(title="b")]
    assert lv.filter(recs, ("title",)) == recs


def test_query_narrows_by_substring_case_insensitive():
    lv = ListView()
    lv.query = "FIX"
    recs = [_rec(id="1", title="Fix the thing"), _rec(id="2", title="Add feature")]
    out = lv.filter(recs, ("title",))
    assert [r["id"] for r in out] == ["1"]


def test_query_searches_every_given_field():
    lv = ListView()
    lv.query = "abcd"
    recs = [_rec(id="1", title="nothing", id4="abcd"), _rec(id="2", title="nothing")]
    out = lv.filter(recs, ("title", "id4"))
    assert [r["id"] for r in out] == ["1"]


def test_whitespace_only_query_is_treated_as_empty():
    lv = ListView()
    lv.query = "   "
    recs = [_rec(title="a"), _rec(title="b")]
    assert lv.filter(recs, ("title",)) == recs


def test_keep_predicate_survives_a_non_matching_query():
    """The record-shape-contract escape hatch (README, Phase 4): a row the
    caller flags via ``keep`` is never filtered out, even if it wouldn't
    otherwise match the query."""
    lv = ListView()
    lv.query = "zzz-no-match"
    recs = [_rec(id="1", title="live one", mux_live=True),
            _rec(id="2", title="not live")]
    out = lv.filter(recs, ("title",), keep=lambda r: r.get("mux_live"))
    assert [r["id"] for r in out] == ["1"]


def test_sort_with_no_keys_returns_records_unchanged():
    lv = ListView()
    recs = [_rec(id="1"), _rec(id="2")]
    assert lv.sort(recs, []) == recs


def test_sort_uses_the_current_key_and_is_stable():
    lv = ListView()
    keys = [("age", lambda w: w["age"]), ("title", lambda w: w["title"])]
    recs = [_rec(id="1", age=2, title="b"), _rec(id="2", age=1, title="a")]
    by_age = lv.sort(recs, keys)
    assert [r["id"] for r in by_age] == ["2", "1"]
    lv.cycle_sort(keys)
    by_title = lv.sort(recs, keys)
    assert [r["id"] for r in by_title] == ["2", "1"]


def test_cycle_sort_wraps_around():
    lv = ListView()
    keys = [("a", lambda w: 0), ("b", lambda w: 0), ("c", lambda w: 0)]
    assert lv.sort_label(keys) == "a"
    lv.cycle_sort(keys)
    assert lv.sort_label(keys) == "b"
    lv.cycle_sort(keys)
    assert lv.sort_label(keys) == "c"
    lv.cycle_sort(keys)
    assert lv.sort_label(keys) == "a"


def test_cycle_sort_is_a_noop_with_no_keys():
    lv = ListView()
    lv.cycle_sort([])
    assert lv.sort_index == 0
    assert lv.sort_label([]) is None


def test_clear_resets_query_but_not_sort():
    lv = ListView()
    lv.query = "something"
    keys = [("a", lambda w: 0), ("b", lambda w: 0)]
    lv.cycle_sort(keys)
    lv.clear()
    assert lv.query == ""
    assert lv.sort_label(keys) == "b"
