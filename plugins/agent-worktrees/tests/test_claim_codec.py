"""Claim uncertainty survives ordinary load/save and public projections."""

from __future__ import annotations

import pytest

from agent_worktrees import claim_codec, tracking


@pytest.mark.parametrize("state", ["active", "at-rest", "released", "abandoned"])
def test_known_claim_states_keep_existing_wire_shape(state):
    raw = {"kind": "task", "ref": "dispatch-local-task:task-1", "state": state}
    claim = claim_codec.parse_mapping(raw, claim_factory=tracking.ResourceClaim)
    assert claim.metadata_opaque is False
    assert claim.state == state
    encoded = claim_codec.yaml_mapping(claim)
    assert "metadata_opaque" not in encoded
    assert encoded.get("state", "active") == state
    assert "metadata_opaque" not in claim.to_dict()
    assert "metadata_opaque" not in claim.to_public_dict()


@pytest.mark.parametrize("change", [
    {"state": "unknown"}, {"state": None}, {"state": []},
    {"metadata_opaque": True}, {"metadata_opaque": "false"},
    {"kind": None}, {"kind": ""}, {"ref": True}, {"ref": ""},
])
def test_uncertain_claim_metadata_never_roundtrips_into_proof(change):
    raw = {"kind": "task", "ref": "dispatch-local-task:task-1", **change}
    claim = claim_codec.parse_mapping(raw, claim_factory=tracking.ResourceClaim)
    assert claim.metadata_opaque is True
    assert claim.is_live is True
    assert claim.to_dict()["metadata_opaque"] is True
    assert claim.to_public_dict()["metadata_opaque"] is True
    encoded = claim_codec.yaml_mapping(claim)
    assert encoded["metadata_opaque"] is True
    restored = claim_codec.parse_mapping(encoded, claim_factory=tracking.ResourceClaim)
    assert restored.metadata_opaque is True
    assert restored.is_live is True


def test_missing_resources_and_omitted_active_state_keep_legacy_meaning():
    assert claim_codec.parse_list([], claim_factory=tracking.ResourceClaim) == []
    claim = claim_codec.parse_mapping(
        {"kind": "task", "ref": "dispatch-local-task:task-1"},
        claim_factory=tracking.ResourceClaim,
    )
    assert claim.state == "active"
    assert claim.metadata_opaque is False


@pytest.mark.parametrize("raw", [None, {}, "unknown", [None], [{}]])
def test_malformed_collection_retains_one_conservative_placeholder(raw):
    claims = claim_codec.parse_list(raw, claim_factory=tracking.ResourceClaim)
    assert len(claims) == 1
    assert claims[0].metadata_opaque is True
    assert claims[0].is_live is True
    restored = claim_codec.parse_list(
        [claim_codec.yaml_mapping(claim) for claim in claims], claim_factory=tracking.ResourceClaim,
    )
    assert len(restored) == 1
    assert restored[0].metadata_opaque is True
