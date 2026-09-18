"""Tests for the Picker row's `asset_hints` field (#6443/upstream #1979 Phase
6): a bounded per-kind breakdown of a worktree's HELD outbound claims (the
``resources`` ledger), distinct from `status_markers`'s bare ``C<N>`` count --
lets the operator tell a held PR from a child worktree/Codespace/container/
remote session at a glance, with full per-claim detail available for a
width-constrained consumer (the action menu)."""

from __future__ import annotations

from worktree_manager.production_picker.picker_tui import derive


def _raw(**values):
    row = {
        "id": "child",
        "repo": "example",
        "status": "active",
        "started_at": "2026-01-01T00:00:00",
    }
    row.update(values)
    return row


def test_no_resources_yields_empty_hints():
    row = derive.norm(_raw(), "host", "windows")
    assert row["asset_hints"] == {"hints": [], "overflow": 0, "details": []}


def test_single_claim_per_kind_has_no_count_suffix():
    row = derive.norm(_raw(resources=[
        {"kind": "pr", "ref": "https://example/pulls/1", "state": "active"},
    ]), "host", "windows")
    assert row["asset_hints"]["hints"] == ["PR"]
    assert row["asset_hints"]["overflow"] == 0


def test_repeated_kind_gets_a_count_suffix():
    row = derive.norm(_raw(resources=[
        {"kind": "pr", "ref": "https://example/pulls/1", "state": "active"},
        {"kind": "pr", "ref": "https://example/pulls/2", "state": ""},
    ]), "host", "windows")
    assert row["asset_hints"]["hints"] == ["PR×2"]


def test_only_held_claims_count_active_and_at_rest():
    row = derive.norm(_raw(resources=[
        {"kind": "pr", "ref": "held-active", "state": "active"},
        {"kind": "workdir", "ref": "held-at-rest", "state": "at-rest"},
        {"kind": "container", "ref": "released", "state": "released"},
        {"kind": "ssh", "ref": "abandoned", "state": "abandoned"},
    ]), "host", "windows")
    kinds = {d["kind"] for d in row["asset_hints"]["details"]}
    assert kinds == {"pr", "workdir"}
    assert sorted(row["asset_hints"]["hints"]) == ["DIR", "PR"]


def test_hints_are_bounded_with_overflow_count():
    resources = [
        {"kind": "pr", "ref": "1", "state": "active"},
        {"kind": "worktree", "ref": "2", "state": "active"},
        {"kind": "codespace", "ref": "3", "state": "active"},
        {"kind": "container", "ref": "4", "state": "active"},
        {"kind": "ssh", "ref": "5", "state": "active"},
        {"kind": "workdir", "ref": "6", "state": "active"},
    ]
    row = derive.norm(_raw(resources=resources), "host", "windows")
    assert len(row["asset_hints"]["hints"]) == 4
    assert row["asset_hints"]["overflow"] == 2
    # Full detail is never bounded -- the action menu shows everything.
    assert len(row["asset_hints"]["details"]) == 6


def test_unrecognized_kind_degrades_to_an_upper_code_not_dropped():
    row = derive.norm(_raw(resources=[
        {"kind": "future-thing", "ref": "x", "state": "active"},
    ]), "host", "windows")
    assert row["asset_hints"]["hints"] == ["FUTU"]


def test_distinct_kinds_are_counted_separately_before_code_collision():
    """Regression: two DISTINCT unrecognized kinds whose fallback 4-char codes
    collide (both truncate to 'WORK') must still be counted as 2 real claims
    -- and each kind's own detail entry preserved -- even though the display
    hint necessarily merges them into one code token."""
    row = derive.norm(_raw(resources=[
        {"kind": "workspace", "ref": "a", "state": "active"},
        {"kind": "workflow", "ref": "b", "state": "active"},
    ]), "host", "windows")
    assert row["asset_hints"]["hints"] == ["WORK×2"]
    kinds = sorted(d["kind"] for d in row["asset_hints"]["details"])
    assert kinds == ["workflow", "workspace"]


def test_malformed_resource_entries_are_skipped_not_fatal():
    row = derive.norm(_raw(resources=[
        "not-a-dict",
        None,
        {"kind": "pr", "ref": "ok", "state": "active"},
    ]), "host", "windows")
    assert row["asset_hints"]["hints"] == ["PR"]
    assert len(row["asset_hints"]["details"]) == 1
