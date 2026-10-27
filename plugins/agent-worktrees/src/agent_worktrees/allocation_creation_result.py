"""Negotiated ordinary creation results, separate from strict lifecycle proofs."""

from __future__ import annotations

import json

from . import creation_allocation, owned_worktree as allocation
from . import resource_producer as producer, tracking
from .worktrees_projection import MAX_ROW_BYTES

SCHEMA = "copilot-extensions.worktree-allocation-result"
VERSION = 1
MAX_RESPONSE_BYTES = MAX_ROW_BYTES + producer.MAX_INPUT_BYTES


def _encoded(value: object) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        )
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("allocation creation result is not bounded JSON data") from exc


def wire_json(value: dict) -> str:
    """Bound the ordinary row and lifecycle proof without changing their ABIs."""
    encoded = _encoded(value) + "\n"
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("allocation creation result exceeds the negotiated response bound")
    result = value.get("result")
    if isinstance(result, dict) and len(_encoded(result.get("worktree"))) > MAX_ROW_BYTES:
        raise ValueError("allocation creation result exceeds the ordinary row bound")
    return encoded


def response(
    proof: dict, row: dict | None, record: tracking.WorktreeRecord | None,
) -> dict:
    """Return only the original completed journal's actual sessionless result."""
    result = None
    if proof["status"] in {"allocated", "confirmed"} and proof["allocation_finished"] is True:
        if row is None or row["status"] != "complete" or record is None:
            raise ValueError("allocation creation result has no completed original journal")
        saved = row["worktree"]
        worktree = saved.get("worktree") if isinstance(saved, dict) else None
        if not isinstance(worktree, dict) or saved.get("creation_nonce") != record.creation_nonce:
            raise ValueError("allocation creation result has invalid original identity")
        creation_allocation.require_result_identity(record, worktree)
        creation_allocation.require_record_evidence(record, saved.get("record_identity"))
        if (
            worktree.get("id") != record.worktree_id or worktree.get("repo") != record.repo
            or worktree.get("path") != record.worktree_path or worktree.get("branch") != record.branch
            or allocation.AllocationIdentity.from_dict(proof["identity"]).creation_nonce != record.creation_nonce
        ):
            raise ValueError("allocation creation result differs from the original receiving child")
        result = {
            "worktree": worktree,
            "creation": {"version": 1, "status": "complete", "replayed": True},
        }
    value = {
        "schema": SCHEMA, "version": VERSION, "allocation": proof, "result": result,
    }
    wire_json(value)
    return value


def _verified_unfinished(proof: object, metadata: dict) -> bool:
    if (
        not isinstance(proof, dict) or set(proof) != {
            "schema", "version", "origin", "request_id", "token", "target",
            "identity", "status", "allocation_finished",
        }
        or proof["schema"] != allocation.SCHEMA or type(proof["version"]) is not int
        or proof["version"] != allocation.VERSION
        or proof["status"] not in ("pending", "unknown")
        or proof["allocation_finished"] is not False
        or proof["origin"] != metadata["creator"]
        or proof["request_id"] != metadata["request_id"]
        or proof["token"] != metadata["token"]
    ):
        return False
    try:
        producer.canonical_json(proof)
        if producer.canonical_json(proof["target"]) != producer.canonical_json(metadata["recovery"]):
            return False
        if proof["identity"] is not None:
            identity = allocation.AllocationIdentity.from_dict(proof["identity"])
            if allocation.qualified_ref(identity.ref)[:2] != (
                metadata["recovery"]["machine"], metadata["recovery"]["project"],
            ):
                return False
            if "identity" in metadata and identity.to_dict() != metadata["identity"]:
                return False
    except (ValueError, TypeError, KeyError, RecursionError):
        return False
    return True


def validate(value: object, metadata: dict) -> dict | None:
    """Validate the exact receiving proof and ordinary row before consumption."""
    if not allocation.valid_metadata(metadata):
        raise ValueError("invalid original allocation responsibility for creation result")
    if (
        not isinstance(value, dict) or set(value) != {"schema", "version", "allocation", "result"}
        or value["schema"] != SCHEMA or type(value["version"]) is not int
        or value["version"] != VERSION
    ):
        raise ValueError("unsupported allocation creation-result contract")
    wire_json(value)
    proof = value["allocation"]
    if isinstance(proof, dict) and proof.get("status") in ("pending", "unknown"):
        if not _verified_unfinished(proof, metadata) or value["result"] is not None:
            raise ValueError("invalid unfinished allocation creation result")
        return None
    if not allocation.verified_observation(proof, metadata):
        raise ValueError("allocation creation result does not verify the original request")
    result = value["result"]
    if proof["status"] not in {"allocated", "confirmed"}:
        if result is not None:
            raise ValueError("concluded allocation cannot supply a usable creation result")
        return None
    if (
        not isinstance(result, dict) or set(result) != {"worktree", "creation"}
        or result["creation"] != {"version": 1, "status": "complete", "replayed": True}
        or type(result["creation"].get("version")) is not int
        or result["creation"].get("replayed") is not True
        or not isinstance(result["worktree"], dict)
    ):
        raise ValueError("invalid original sessionless creation result")
    identity = allocation.AllocationIdentity.from_dict(proof["identity"])
    machine, project, worktree_id = allocation.qualified_ref(identity.ref)
    worktree = result["worktree"]
    if (
        worktree.get("id") != worktree_id or worktree.get("repo") != project
        or worktree.get("execution_machine", worktree.get("machine")) != machine
        or worktree.get("creation_nonce") != identity.creation_nonce
        or worktree.get("path") != identity.path or worktree.get("branch") != identity.branch
        or worktree.get("owner_ref") != metadata["creator"]
    ):
        raise ValueError("ordinary creation result names a different receiving incarnation")
    return result
