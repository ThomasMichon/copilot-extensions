"""Legacy receipts and tuple-backed reciprocal execution ownership."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent_worktrees import terminal_pair_progress as progress, tracking
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity


def _pair(tmp_path: Path, *, versioned: bool):
    identity = WorktreeMachineIdentity("desk", "wsl", "guest-space") if versioned else None
    machine = identity.machine if identity else "desk-wsl"
    execution_owner = identity.execution_machine if identity else machine
    records = [
        tracking.create_new_record(
            name, f"worktree/{name}", str(tmp_path / name), project, machine,
            "wsl", tmp_path / project / "worktrees", machine_identity=identity,
        )
        for name, project in (("primary", "harness"), ("sibling", "knowledge"))
    ]
    for record, other, role in (
        (records[0], records[1], "harness"), (records[1], records[0], "knowledge"),
    ):
        record.owner, record.kind, record.status, record.origin = (
            "dispatcher", "bridge", "finalized" if role == "knowledge" else "complete", "delegate",
        )
        record.pair_id, record.pair_role, record.pair_kind = "pair", role, "worktree"
        record.pair_ref = tracking.format_claim_ref(
            execution_owner, other.repo, other.worktree_id,
        )
        tracking.save_record(record, record.yaml_path)
    return records


@pytest.mark.parametrize("versioned", [False, True])
def test_retained_pair_receipts_resume_and_allow_completed_discharge(tmp_path, monkeypatch, versioned):
    monkeypatch.setattr(tracking.cfg, "project_dir", lambda project=None: tmp_path / str(project))
    primary, sibling = _pair(tmp_path, versioned=versioned)
    progress.publish(primary, sibling, sibling.yaml_path, primary_path=primary.yaml_path)
    receipt = json.loads(progress.receipt_path(sibling.yaml_path).read_text())
    if versioned:
        assert receipt["primary"] | primary.machine_fields == receipt["primary"]
    else:
        old_identity = {
            "machine": "desk-wsl", "repo": "harness", "worktree_id": "primary",
            "worktree_path": str(tmp_path / "primary"), "branch": "worktree/primary",
            "pair": {
                "pair_id": "pair", "pair_role": "harness",
                "pair_ref": "desk-wsl/knowledge/sibling", "pair_kind": "worktree",
            },
            "dispatch_attempt": None,
        }
        assert receipt["primary"] == old_identity
        receipt["primary"] = old_identity
        progress.receipt_path(sibling.yaml_path).write_text(json.dumps(receipt))

    unpaired_sibling = replace(
        sibling, pair_id=None, pair_role=None, pair_ref=None, pair_kind=None,
    )
    tracking.save_record(unpaired_sibling, sibling.yaml_path)
    recovered = progress.reciprocal_snapshot(primary, unpaired_sibling, sibling.yaml_path)
    assert recovered is not None
    assert recovered.pair_ref == sibling.pair_ref
    assert progress.reciprocal_identity_valid(
        progress.primary_identity(primary), progress.primary_identity(recovered),
    )
    assert progress.gc_preservation_reason(sibling.yaml_path) == "terminal-pair-discharge-pending"
    unpaired_primary = replace(
        primary, pair_id=None, pair_role=None, pair_ref=None, pair_kind=None,
    )
    tracking.save_record(unpaired_primary, primary.yaml_path)
    assert progress.gc_preservation_reason(sibling.yaml_path) is None
    assert progress.gc_preservation_reason(primary.yaml_path) == "terminal-pair-sibling-gc-pending"


def test_tuple_pair_uses_execution_owner_and_rejects_unknown_metadata(tmp_path):
    primary, sibling = _pair(tmp_path, versioned=True)
    left, right = progress.primary_identity(primary), progress.primary_identity(sibling)
    assert progress.reciprocal_identity_valid(left, right)
    wrong_owner = {**left, "pair": {**left["pair"], "pair_ref": "desk/knowledge/sibling"}}
    assert not progress.reciprocal_identity_valid(wrong_owner, right)
    assert not progress.reciprocal_identity_valid(
        left, {**right, "machine_identity_version": 2},
    )
