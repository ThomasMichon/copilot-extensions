"""Tests for the Picker row's `status_markers` field (worktree-finality-and-
obligations Phase 9): the closure descriptor's per-fact freshness markers
(C<N>/F<N> held-claim/follow-up counts, U*/OC* unconfirmed-fact markers)
surfaced on the worktree list's always-on second (detail) row."""

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


def test_no_closure_yields_no_markers() -> None:
    row = derive.norm(_raw(), "host", "windows")
    assert row["status_markers"] == ""


def test_final_label_has_no_markers() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure={"label": "FINAL", "compact": "FINAL"},
    ), "host", "windows")
    assert row["status_markers"] == ""


def test_merged_with_held_claim_and_unconfirmed_facts() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure={"label": "MERGED", "compact": "MERGED C1 U* OC*"},
    ), "host", "windows")
    assert row["status_markers"] == "C1 U* OC*"


def test_markers_never_include_the_base_label_itself() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure={"label": "MERGED", "compact": "MERGED OC*"},
    ), "host", "windows")
    assert "MERGED" not in row["status_markers"]
    assert row["status_markers"] == "OC*"


def test_mismatched_compact_prefix_degrades_to_no_markers() -> None:
    # A malformed/mixed-version descriptor whose compact doesn't actually
    # start with its own label must never be mis-sliced into garbage --
    # degrade to no markers rather than guessing.
    row = derive.norm(_raw(
        state="completed",
        closure={"label": "MERGED", "compact": "UNEXPECTED"},
    ), "host", "windows")
    assert row["status_markers"] == ""
