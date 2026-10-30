"""Prepare HTTP-51 receipt-capability declarations, never capture provenance."""

from __future__ import annotations

import copy
from pathlib import Path

import agent_bridge_contract_registry as codec
import bridge_http51_evidence as evidence
from agent_bridge_contract_publication import publish

REFERENCE = "plugins/agent-bridge/docs/delegation-contract.md"
DEFERRED = "agent-bridge.delegation-control"


def declaration_plan(root: Path, commit: str, snapshot: codec.Snapshot) -> dict[str, bytes]:
    """Bind the explicit semantic owners to genuine committed source bytes."""
    updated = copy.deepcopy(snapshot.materialized)
    http = evidence.contract(updated)
    sources = {row["path"]: row for row in http["source_paths"]}
    allowed = set(evidence.AFFECTED)
    for path in sorted(allowed | sources.keys()):
        row = sources.get(path)
        actual_path = row.get("current_path", path) if row is not None else path
        data = evidence.safe(root, actual_path).read_bytes()
        sha256 = evidence.previous.git_evidence.sha256_bytes(data)
        if path not in allowed:
            if sha256 != row["sha256"]:
                raise ValueError(f"unclassified HTTP-51 source drift: {path}")
            continue
        committed = evidence.previous.git(root, "show", commit + ":" + actual_path)
        if evidence.previous.git_evidence.sha256_bytes(committed) != sha256:
            raise ValueError(f"dirty HTTP-51 declaration source: {actual_path}")
        if row is None:
            row = {"path": path}
            http["source_paths"].append(row)
        if "current_path" in row:
            raise ValueError(f"HTTP-51 semantic owner cannot be a relocated source: {path}")
        row.update(sha256=sha256, semantic=True, non_semantic_reason=None)
    for owner in updated["contracts"]:
        if owner is http:
            continue
        for row in owner["source_paths"]:
            data = evidence.safe(root, row.get("current_path", row["path"])).read_bytes()
            if evidence.previous.git_evidence.sha256_bytes(data) != row["sha256"]:
                raise ValueError(f"foreign contract source drift: {row['path']}")
    reference = next(row for row in updated["deferred_contracts"] if row["id"] == DEFERRED)
    if reference.get("reference_path") != REFERENCE:
        raise ValueError("HTTP-51 deferred documentation owner changed")
    document = evidence.safe(root, REFERENCE).read_bytes()
    committed = evidence.previous.git(root, "show", commit + ":" + REFERENCE)
    if evidence.previous.git_evidence.sha256_bytes(document) != (
        evidence.previous.git_evidence.sha256_bytes(committed)
    ):
        raise ValueError("dirty HTTP-51 deferred documentation reference")
    reference["reference_sha256"] = evidence.previous.git_evidence.sha256_bytes(document)
    outputs = codec.plan(root, snapshot, updated)
    if set(outputs) != {codec.REGISTRY}:
        raise ValueError("declaration preparation cannot archive or create capture evidence")
    return outputs


def prepare(root: Path, commit: str) -> dict[str, bytes]:
    """Publish declarations only; a subsequent committed checkpoint must capture."""
    evidence.baseline(root, commit)
    snapshot = codec.load(root)
    outputs = declaration_plan(root, commit, snapshot)
    inputs = {path: codec.digest(data) for path, data in snapshot.inputs.items()}
    for owner in snapshot.materialized["contracts"]:
        for row in owner["source_paths"]:
            path = row.get("current_path", row["path"])
            inputs[path] = codec.digest(evidence.safe(root, path).read_bytes())
        for row in owner["fixtures"]:
            data = evidence.safe(root, row["path"]).read_bytes()
            if evidence.previous.git_evidence.sha256_bytes(data) != row["sha256"]:
                raise ValueError(f"immutable capture fixture drift: {row['path']}")
            inputs[row["path"]] = codec.digest(data)
    for row in snapshot.materialized["deferred_contracts"]:
        if "reference_path" in row:
            path = row["reference_path"]
            data = evidence.safe(root, path).read_bytes()
            if path != REFERENCE and (
                evidence.previous.git_evidence.sha256_bytes(data) != row["reference_sha256"]
            ):
                raise ValueError(f"foreign deferred reference drift: {path}")
            inputs[path] = codec.digest(data)

    def validate() -> None:
        current = codec.load(root)
        if current.materialized not in (
            snapshot.materialized,
            codec.load(
                root, read=lambda path: outputs[path] if path in outputs else snapshot.inputs[path],
            ).materialized,
        ):
            raise ValueError("HTTP-51 declarations changed during preparation")
        if declaration_plan(root, commit, snapshot) != outputs:
            raise ValueError("HTTP-51 declaration source changed during preparation")

    publish(root, commit, outputs, validate=validate, input_hashes=inputs)
    return outputs
