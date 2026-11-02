"""Reuse protected allocation responsibility when ordinary creation is wrapped by run."""

from __future__ import annotations

import json

from . import owned_worktree as allocation, resource_producer as producer, tracking
from .worktree_machine_identity import execution_machine_from_fields


def existing_claim(record: tracking.WorktreeRecord, stdout: str) -> tracking.ResourceClaim | None:
    """Never add a competing plain child claim beside asynchronous allocation linkage."""
    try:
        value = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or "allocation" not in value:
        return None
    result, row = value["allocation"], value.get("worktree")
    if not isinstance(result, dict):
        raise ValueError("run: invalid ordinary allocation result; pending ownership retained")
    owner = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    if (
        type(result.get("version")) is not int or result["version"] != 1
        or result.get("creator") != owner or not isinstance(result.get("request_id"), str)
    ):
        raise ValueError("run: allocation result differs from the original owner")
    matches = [
        claim for claim in record.resources
        if isinstance(claim.producer, dict)
        and claim.producer.get("lifetime") == allocation.LIFETIME
        and claim.producer.get("request_id") == result["request_id"]
    ]
    if len(matches) != 1:
        raise ValueError("run: original allocation responsibility is missing or ambiguous")
    claim = matches[0]
    metadata = claim.producer
    if (
        claim.kind != "worktree" or not allocation.valid_metadata(metadata)
        or allocation.claim_ref(metadata) != claim.ref or metadata["creator"] != owner
        or producer.canonical_json(metadata["recovery"]) != producer.canonical_json(result.get("target"))
    ):
        raise ValueError("run: original allocation responsibility does not verify this result")
    if row is None:
        creation = value.get("creation")
        if (
            not isinstance(creation, dict) or type(creation.get("version")) is not int
            or creation["version"] != 1 or creation.get("status") not in {"pending", "terminal"}
            or creation.get("repeat_new") is not False
        ):
            raise ValueError("run: incomplete allocation response does not retain original-operation uncertainty")
        return claim
    if (
        not isinstance(row, dict) or row.get("owner_ref") != owner
        or result.get("receiving_status") not in {"allocated", "confirmed"}
    ):
        raise ValueError("run: invalid ordinary receiving result")
    machine, project = execution_machine_from_fields(row), row.get("repo")
    identity = allocation.AllocationIdentity.from_dict({
        "ref": tracking.format_claim_ref(machine, project, row.get("id")),
        "creation_nonce": row.get("creation_nonce"), "path": row.get("path"), "branch": row.get("branch"),
    })
    if (machine, project) != (metadata["recovery"]["machine"], metadata["recovery"]["project"]):
        raise ValueError("run: receiving result names another allocation destination")
    if "identity" in metadata and metadata["identity"] != identity.to_dict():
        raise ValueError("run: receiving result names another child incarnation")
    return claim
