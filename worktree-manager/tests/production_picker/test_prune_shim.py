"""worktree-finality-and-obligations Phase 5: direct coverage for the Manager-
owned closure-descriptor shim (``production_picker/prune.py``).

Exercises the shim directly (valid, malformed-shape, scalar-type, and
label/final-mismatch payloads) and the Picker's ``derive.norm``/``_state``/
``_status_markers`` driven through it, including the unclassified-legacy-row
gate -- so this Manager-owned copy can't silently drift from
``agent_worktrees/prune.py``'s copy while only one stays covered.
"""
from __future__ import annotations

from worktree_manager.production_picker import prune
from worktree_manager.production_picker.picker_tui import derive


def _valid_final_payload(**overrides):
    payload = {
        "version": prune.DESCRIPTOR_VERSION,
        "label": "FINAL",
        "style": "final",
        "compact": "FINAL",
        "claims": {"held": 0},
        "follow_ups": {"open": 0},
        "closure": {"final": True},
        "action": {"disposition": "safe", "bucket": "clean"},
    }
    payload.update(overrides)
    return payload


def _raw(**kw):
    base = {"id": "anomalous-potato-win-20260715-0000-abcd",
            "machine": "anomalous-potato", "title": "Feeder cam",
            "status": "finalized", "state": "completed"}
    base.update(kw)
    return base


class TestProductionShimTrackedCanonicalVersion:
    """The shim's own hardcoded ``DESCRIPTOR_VERSION`` can silently drift from
    ``agent_worktrees.prune``'s -- this guard fails CI immediately on that
    drift (both packages are importable side-by-side in this monorepo
    dev/CI environment, even though the deployed Manager runtime doesn't
    depend on ``agent_worktrees`` at runtime -- see the shim's module
    docstring)."""

    def test_descriptor_version_matches_the_canonical_copy(self):
        from agent_worktrees import prune as canonical_prune

        assert prune.DESCRIPTOR_VERSION == canonical_prune.DESCRIPTOR_VERSION


class TestProductionShimInterpretDescriptorPayload:
    def test_valid_final_payload_is_trusted(self):
        interpreted = prune.interpret_descriptor_payload(_valid_final_payload())
        assert interpreted["supported"] is True
        assert interpreted["final"] is True
        assert interpreted["label"] == "FINAL"

    def test_version_skew_is_never_trusted(self):
        payload = _valid_final_payload(version=999)
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False
        assert interpreted["label"] == "UNKNOWN"

    def test_missing_closure_is_never_trusted(self):
        payload = _valid_final_payload()
        del payload["closure"]
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_missing_compact_is_never_trusted(self):
        # Regression: a truncated payload with a `label` but no `compact` must
        # not default to the label -- that would let a truncated FINAL payload
        # (label present, compact absent) still render as a supported FINAL.
        payload = _valid_final_payload()
        del payload["compact"]
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_wrong_shaped_closure_is_never_trusted(self):
        payload = _valid_final_payload(closure=[])
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_empty_closure_with_final_label_is_rejected(self):
        payload = _valid_final_payload(closure={})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False
        assert interpreted["label"] == "UNKNOWN"

    def test_string_final_is_never_truthy_coerced(self):
        payload = _valid_final_payload(closure={"final": "false"})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_non_numeric_claim_count_is_rejected(self):
        payload = _valid_final_payload(claims={"held": "not-a-number"})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_final_with_held_claims_is_rejected(self):
        payload = _valid_final_payload(claims={"held": 2})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_final_with_non_safe_action_is_rejected(self):
        payload = _valid_final_payload(action={"disposition": "blocked"})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_non_final_with_blockers_stays_supported(self):
        payload = _valid_final_payload(
            label="MERGED", style="merged-blocked", closure={"final": False},
            claims={"held": 2}, action={"disposition": "blocked"})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is True
        assert interpreted["held_claims"] == 2


class TestProductionDeriveNormThroughShim:
    """The Picker's ``derive.norm``, driven through THIS package's ``prune``
    shim (not the plugin's)."""

    def test_valid_final_payload_renders_final(self):
        n = derive.norm(_raw(closure=_valid_final_payload()), "anomalous-potato", "win")
        assert n["state"] == "FINAL"

    def test_version_skewed_payload_degrades_to_merged(self):
        payload = _valid_final_payload(version=999)
        n = derive.norm(_raw(closure=payload), "anomalous-potato", "win")
        assert n["state"] == "MERGED"
        assert n["status_markers"] == ""

    def test_malformed_closure_degrades_to_merged(self):
        n = derive.norm(_raw(closure="not-a-dict"), "anomalous-potato", "win")
        assert n["state"] == "MERGED"

    def test_no_closure_at_all_degrades_to_merged(self):
        n = derive.norm(_raw(), "anomalous-potato", "win")
        assert n["state"] == "MERGED"

    def test_markers_present_for_a_valid_merged_descriptor(self):
        payload = _valid_final_payload(
            label="MERGED", style="merged-blocked", compact="MERGED C2 F1",
            closure={"final": False}, claims={"held": 2},
            follow_ups={"open": 1}, action={"disposition": "blocked"})
        n = derive.norm(_raw(closure=payload), "anomalous-potato", "win")
        assert n["state"] == "MERGED"
        assert n["status_markers"] == "C2 F1"

    def test_unclassified_legacy_row_also_gated(self):
        # No canonical ``state`` field at all (an older remote) -- the
        # unclassified-legacy fallback must ALSO route through the shim
        # rather than trusting a raw FINAL claim.
        n = derive.norm(
            {"id": "x", "status": "finalized"}, "anomalous-potato", "win")
        assert n["state"] == "MERGED"
