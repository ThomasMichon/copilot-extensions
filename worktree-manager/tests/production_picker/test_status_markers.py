"""Tests for the Picker row's `status_markers` field (worktree-finality-and-
obligations Phase 9): the closure descriptor's per-fact freshness markers
(C<N>/F<N> held-claim/follow-up counts, U*/OC* unconfirmed-fact markers)
surfaced on the worktree list's always-on second (detail) row."""

from __future__ import annotations

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker import prune


def _raw(**values):
    row = {
        "id": "child",
        "repo": "example",
        "status": "active",
        "started_at": "2026-01-01T00:00:00",
    }
    row.update(values)
    return row


def _closure(**overrides):
    payload = {
        "version": prune.DESCRIPTOR_VERSION,
        "label": "FINAL",
        "style": "final",
        "compact": "FINAL",
        "claims": {"held": 0},
        "follow_ups": {"open": 0},
        "closure": {"final": True},
        "action": {"disposition": "safe"},
    }
    payload.update(overrides)
    return payload


def test_no_closure_yields_no_markers() -> None:
    row = derive.norm(_raw(), "host", "windows")
    assert row["status_markers"] == ""


def test_final_label_has_no_markers() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(),
    ), "host", "windows")
    assert row["status_markers"] == ""


def test_state_style_carried_through_for_supported_final_descriptor() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(),
    ), "host", "windows")
    assert row["state_style"] == "final"


def test_state_style_carried_through_for_supported_merged_descriptor() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(
            label="MERGED", style="merged-blocked", compact="MERGED",
            closure={"final": False}),
    ), "host", "windows")
    assert row["state_style"] == "merged-blocked"


def test_state_style_absent_without_a_closure_descriptor() -> None:
    row = derive.norm(_raw(), "host", "windows")
    assert row["state_style"] is None


def test_state_style_absent_for_unsupported_descriptor() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(version=prune.DESCRIPTOR_VERSION + 1),
    ), "host", "windows")
    assert row["state_style"] is None


def test_merged_with_held_claim_and_unconfirmed_facts() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(
            label="MERGED", style="merged-blocked", compact="MERGED C1 U* OC*",
            claims={"held": 1}, closure={"final": False},
            action={"disposition": "blocked"}),
    ), "host", "windows")
    assert row["status_markers"] == "C1 U* OC*"


def test_markers_never_include_the_base_label_itself() -> None:
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(
            label="MERGED", style="merged-blocked", compact="MERGED OC*",
            closure={"final": False}, action={"disposition": "blocked"}),
    ), "host", "windows")
    assert "MERGED" not in row["status_markers"]
    assert row["status_markers"] == "OC*"


def test_mismatched_compact_prefix_degrades_to_no_markers() -> None:
    # A malformed/mixed-version descriptor whose compact doesn't actually
    # start with its own label must never be mis-sliced into garbage --
    # degrade to no markers rather than guessing.
    row = derive.norm(_raw(
        state="completed",
        closure=_closure(
            label="MERGED", style="merged-blocked", compact="UNEXPECTED",
            closure={"final": False}, action={"disposition": "blocked"}),
    ), "host", "windows")
    assert row["status_markers"] == ""


def test_unsupported_descriptor_yields_no_markers() -> None:
    # An absent/malformed/version-skewed descriptor must never be laundered
    # into markers -- only a fully validated, supported payload can.
    row = derive.norm(_raw(
        state="completed",
        closure={"label": "MERGED", "compact": "MERGED C1"},
    ), "host", "windows")
    assert row["status_markers"] == ""

