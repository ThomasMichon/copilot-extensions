"""worktree-finality-and-obligations Phase 5: production-side coverage for the
Manager-owned closure-descriptor shim (PR #2738 review response).

``worktree_manager/production_picker/prune.py`` is a second, independent
implementation of ``agent_worktrees/prune.py``'s
``interpret_descriptor_payload`` (needed because the transplanted
``picker_tui/derive.py`` resolves ``from .. import prune`` inside THIS
package, not the plugin's). The plugin's own test suite exercising its copy
does not exercise this one -- these tests send a valid, malformed, and
version-skewed closure payload through both the shim directly and through
``worktree_manager.production_picker.picker_tui.derive.norm``, so the two
copies can't silently drift apart while only one stays covered.
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
    """PR #2738 review response: the shim's own hardcoded ``DESCRIPTOR_VERSION``
    can silently drift from ``agent_worktrees.prune``'s -- if the plugin bumps
    its schema and this copy doesn't follow, a deployed Manager would reject
    every real descriptor as unsupported. This guard fails CI immediately on
    that drift (both packages are importable in this monorepo dev/CI
    environment, even though the deployed Manager runtime doesn't depend on
    ``agent_worktrees`` at runtime -- see the module docstring)."""

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
        # PR #2738 review response: a malformed count must never be
        # laundered into a coerced zero (false evidence of no blockers).
        payload = _valid_final_payload(claims={"held": "not-a-number"})
        interpreted = prune.interpret_descriptor_payload(payload)
        assert interpreted["supported"] is False

    def test_final_with_held_claims_is_rejected(self):
        # PR #2738 review response: a genuine descriptor only ever sets
        # final=True alongside zero held/open counts and a safe action.
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
    """The transplanted ``derive.norm``, driven through THIS package's
    ``prune`` shim (not the plugin's)."""

    def test_valid_final_payload_renders_final(self):
        n = derive.norm(_raw(closure=_valid_final_payload()), "anomalous-potato", "win")
        assert n["state"] == "FINAL"

    def test_version_skewed_payload_degrades_to_merged(self):
        payload = _valid_final_payload(version=999)
        n = derive.norm(_raw(closure=payload), "anomalous-potato", "win")
        assert n["state"] == "MERGED"
        assert n["state_markers"] == ""
        assert n["state_style"] == ""

    def test_malformed_closure_degrades_to_merged(self):
        n = derive.norm(_raw(closure="not-a-dict"), "anomalous-potato", "win")
        assert n["state"] == "MERGED"

    def test_no_closure_at_all_degrades_to_merged(self):
        n = derive.norm(_raw(), "anomalous-potato", "win")
        assert n["state"] == "MERGED"

    def test_markers_and_style_present_for_a_valid_descriptor(self):
        payload = _valid_final_payload(
            label="MERGED", style="merged-blocked", closure={"final": False},
            claims={"held": 2}, follow_ups={"open": 1})
        n = derive.norm(_raw(closure=payload), "anomalous-potato", "win")
        assert n["state"] == "MERGED"
        assert n["state_markers"] == "C2 F1"
        assert n["state_style"] == "merged-blocked"

    def test_unclassified_legacy_row_also_gated(self):
        # No canonical ``state`` field at all (an older remote) -- the
        # unclassified-legacy fallback must ALSO route through the shim
        # rather than trusting a raw FINAL claim.
        n = derive.norm(
            {"id": "x", "status": "finalized"}, "anomalous-potato", "win")
        assert n["state"] == "MERGED"
