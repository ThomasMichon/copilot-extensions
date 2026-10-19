"""Tests for the Picker row's `asset_hints` field (#6443/upstream #1979 Phase
6): a bounded per-kind breakdown of a worktree's HELD outbound claims (the
``resources`` ledger), distinct from `status_markers`'s bare ``C<N>`` count --
lets the operator tell a held PR from a child worktree/Codespace/container/
remote session at a glance, with full per-claim detail available for a
width-constrained consumer (the action menu)."""

from __future__ import annotations

import pytest

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


def test_current_aggregate_preserves_hints_without_enumerating_a_ledger():
    class NeverRead(list):
        def __iter__(self):
            raise AssertionError("current presentation enumerated the ledger")

    row = derive.norm(_raw(
        claims_assets={"held_count": 1101, "by_kind": {"worktree": 1100, "pr": 1},
                       "details_available": True},
        resources=NeverRead(),
    ), "host", "windows")
    assert row["asset_hints"] == {
        "hints": ["PR", "WT\u00d71100"], "overflow": 0, "details": [],
        "held_count": 1101, "details_available": True,
    }


def test_current_aggregate_keeps_code_collisions_and_overflow():
    kinds = {"workspace": 2, "workflow": 3, "pr": 1, "worktree": 1,
             "codespace": 1, "container": 1, "ssh": 1}
    row = derive.norm(_raw(claims_assets={
        "held_count": 10, "by_kind": kinds, "details_available": True,
    }), "host", "windows")
    assert row["asset_hints"]["hints"] == ["PR", "WT", "CS", "CTR"]
    assert row["asset_hints"]["overflow"] == 2
    assert row["asset_hints"]["held_count"] == 10


@pytest.mark.parametrize("summary", [
    {}, [],
    {"held_count": 3, "by_kind": {"pr": 2}, "details_available": True},
    {"held_count": 1, "by_kind": {"pr": True}, "details_available": True},
    {"held_count": 0, "by_kind": {}, "details_available": True},
])
def test_malformed_current_aggregate_is_explicit_not_no_claims(summary):
    with pytest.raises(ValueError, match="held-claim summary"):
        derive.norm(_raw(claims_assets=summary), "host", "windows")


def _projected(**values):
    return _raw(
        claims_summary="dependency#42",
        claims_links=[
            {"label": "dependency#42", "url": "https://github.com/example/dependency/pull/42"},
            {"label": "other#42", "url": "https://github.com/example/other/pull/42"},
        ],
        **values,
    )


def test_claim_completeness_survives_normalization_and_retains_all_link_identities():
    from worktree_manager.production_picker.picker_tui.engine_helpers import _claims_cell

    projection = {"inherited_pr_count": 2, "unresolved_count": 1, "complete": False}
    raw = _projected(
        claims_link_count=15, claims_links_complete=False, claims_projection=projection,
    )
    row = derive.norm(raw, "host", "windows")
    assert row["claims_link_count"] == 15
    assert row["claims_links_complete"] is False
    assert row["claims_projection"] == projection
    assert row["claims_links"] == raw["claims_links"]
    cell = _claims_cell(row, 12)
    assert cell.plain.startswith("[+13 omitted; 1 unresolved]")
    urls = [span.style for span in cell.spans if "link " in str(span.style)]
    assert len(urls) == 2
    assert all(any(link["url"] in str(style) for style in urls) for link in raw["claims_links"])
    assert cell.plain.index("dependency#42") < cell.plain.index("other#42")


@pytest.mark.parametrize("fields, expected", [
    ({}, "coverage unknown"),
    ({"claims_links_complete": False, "claims_link_count": 2}, "incomplete"),
    ({"claims_links_complete": True, "claims_link_count": 2}, ""),
    ({"claims_links_complete": False, "claims_link_count": 2,
      "claims_projection": {"complete": False, "unresolved_count": 3,
                            "inherited_pr_count": 0}}, "3 unresolved"),
    ({"claims_links_complete": False, "claims_link_count": 2,
      "claims_projection": {"complete": False}}, "descendants incomplete; coverage unknown"),
    ({"claims_links_complete": True, "claims_link_count": True}, "coverage unknown"),
])
def test_claim_coverage_distinguishes_unknown_legacy_and_incomplete(fields, expected):
    from worktree_manager.production_picker.picker_tui.worktree_assets import claim_coverage

    row = derive.norm(_projected(**fields), "host", "windows")
    assert "; ".join(claim_coverage(row)) == expected


def test_legacy_producer_without_descendant_evidence_is_not_complete():
    from worktree_manager.production_picker.picker_tui.worktree_assets import claim_coverage

    row = derive.norm(_projected(
        claims_link_count=2, claims_links_complete=True,
        claims_assets={"held_count": 1, "by_kind": {"worktree": 1},
                       "details_available": True},
    ), "host", "windows")
    assert claim_coverage(row) == ["coverage unknown"]


def test_legacy_child_without_links_is_unknown_not_an_empty_complete_subtree():
    from worktree_manager.production_picker.picker_tui.engine_helpers import _claims_cell

    row = derive.norm(_raw(resources=[
        {"kind": "worktree", "ref": "host/repo/child", "state": "active"},
    ]), "host", "windows")
    assert "coverage unknown" in _claims_cell(row, 12).plain


@pytest.mark.parametrize("counts", [None, [], "unknown"])
def test_descendant_kind_guard_keeps_nonmapping_evidence_unknown(counts):
    from worktree_manager.production_picker.picker_tui.worktree_assets import needs_descendant_details

    assert needs_descendant_details(_raw(claims_assets={"by_kind": counts})) is True


@pytest.mark.parametrize("count", [None, 0, 3])
def test_partial_claim_summary_survives_admission_and_normalization_as_unknown(count):
    from worktree_manager.production_picker.picker_tui.engine_helpers import _claims_cell
    from worktree_manager.production_picker.picker_tui.worktree_details_screen import WtDetailsScreen
    from worktree_manager.worktree_rows_contract import parse_current_rows

    summary = {"by_kind": None}
    if count is not None:
        summary["held_count"] = count
    raw = _projected(
        claims_assets=summary, claims_link_count=2, claims_links_complete=True,
        claims_projection={"inherited_pr_count": 0, "unresolved_count": 0, "complete": True},
    )
    raw = parse_current_rows({
        "ok": True, "contract_version": 1, "worktrees": [raw],
        "projection": {
            "schema_version": 1, "quality": "current", "generation": "a" * 32,
            "revision": 1, "observed_at": 1, "roster_count": 1, "selected_count": 1,
        },
    })[0]
    row = derive.norm(raw, "host", "windows")
    assert row["claims_descendants_available"] is True
    assert row["asset_hints"]["coverage_unknown"] is True
    assert row["asset_hints"]["details_available"] is True
    assert row["asset_hints"].get("held_count") == count
    assert "coverage unknown" in _claims_cell(row, 12).plain
    screen = WtDetailsScreen(row)
    expected = "unknown" if count is None else str(count)
    assert f"held claims ({expected}): loading full details" in screen._body().plain


def test_current_claim_evidence_survives_local_and_ssh_adapters(monkeypatch):
    import json
    from types import SimpleNamespace

    from worktree_manager import engine_client
    from worktree_manager.production_picker import context
    from worktree_manager.production_picker.picker_tui import data_local, data_ssh

    raw = _projected(
        claims_link_count=9, claims_links_complete=False,
        claims_projection={"complete": False, "unresolved_count": 1, "inherited_pr_count": 2},
    )
    monkeypatch.setattr(engine_client, "list_worktree_rows", lambda *a, **kw: [raw])
    monkeypatch.setattr(context, "project", lambda: "project")
    local = data_local.load("host", "windows", runner="selected-provider")[0]
    head = {
        "schema_version": 1, "quality": "current", "generation": "a" * 32,
        "revision": 1, "observed_at": 1, "roster_count": 1, "selected_count": 1,
    }
    envelope = {"ok": True, "contract_version": 1, "projection": head, "worktrees": [raw]}
    source = data_ssh.Source("remote", "linux", ["selected-transport"], ready=True)
    remote = data_ssh._fetch(source, runner=lambda *a: SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps(envelope),
    ))[0]
    for key in ("claims_links", "claims_link_count", "claims_links_complete", "claims_projection"):
        assert local[key] == remote[key] == raw[key]


def test_current_rows_still_reject_historical_claim_graphs():
    from worktree_manager.worktree_rows_contract import _bounded

    with pytest.raises(ValueError, match="historical"):
        _bounded({"claims_graph": [{"owner": "child"}]})


def test_bounded_overflow_is_visible_without_reading_raw_ledgers_during_paint():
    from worktree_manager.production_picker.picker_tui.engine_helpers import row_text

    class NeverRead(list):
        def __iter__(self):
            raise AssertionError("paint inspected a ledger")

    raw = _raw(
        claims_links=[{"label": f"repo#{i}", "url": f"https://example.com/pull/{i}"}
                      for i in range(8)],
        claims_link_count=21, claims_links_complete=False,
        claims_projection={"inherited_pr_count": 20, "unresolved_count": 0, "complete": True},
    )
    row = derive.norm(raw, "host", "windows")
    raw["resources"] = NeverRead()
    cell = row_text(row, [("claims_summary", "claims", 12, "l")], 30, False)
    assert "+13 omitted" in cell.plain
    assert len(row["claims_links"]) == 8
