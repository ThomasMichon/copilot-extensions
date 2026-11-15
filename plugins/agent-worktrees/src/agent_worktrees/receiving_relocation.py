"""Original receiving provenance and its first owner-serialized location revision."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

from . import checkout_relocation_state as location, owned_worktree as allocation
from . import resource_producer as producer


def _receipt_digest(record) -> str:
    return hashlib.sha256(producer.canonical_json(record.receiving_allocation).encode()).hexdigest()


def _retained_revision(record, original: allocation.AllocationIdentity) -> dict | None:
    from .checkout_relocation import _identity_matches

    revision = location.read(record.yaml_path)
    if revision is None:
        return None
    proof = revision.get("receiving_location")
    receipt = record.receiving_allocation
    metadata = {
        "creator": receipt["origin"], "request_id": receipt["request_id"],
        "token": receipt["token"], "recovery": receipt["target"],
        "identity": original.to_dict(),
    }
    if (
        revision["schema"] != location.RECEIVING_SCHEMA
        or not isinstance(proof, dict) or set(proof) != {
            "version", "original_identity", "receipt_sha256", "origin_closeout",
        }
        or type(proof["version"]) is not int or proof["version"] != 1
        or proof["original_identity"] != original.to_dict()
        or proof["receipt_sha256"] != _receipt_digest(record)
        or not allocation.verified_conclusion(proof["origin_closeout"], metadata)
        or not _identity_matches(record, revision["identity"])
        or revision["source"] != original.path
    ):
        raise ValueError("receiving current-location revision conflicts with original provenance")
    return revision


def require_current_location(record, original: allocation.AllocationIdentity) -> None:
    """Classify a journaled location, not owner inactivity or mutation permission."""
    revision = _retained_revision(record, original)
    if (
        revision is None or revision["destination"] != record.worktree_path
        or revision["phase"] not in {"git-repaired", "rebound", "retiring", "complete"}
    ):
        raise ValueError("receiving checkout has no proven current-location revision")


def require_retained_provenance(ledger: Path) -> None:
    """Keep unknown/missing receiving evidence out of generic lifecycle writers."""
    from . import receiving_allocation as receiving
    from .checkout_relocation_plan import read_record

    record = read_record(ledger)
    if not record.receiving_allocation_present:
        raise ValueError("relocated receiving record is missing its original allocation receipt")
    admission = receiving.parsed_receipt(record)
    original = allocation.AllocationIdentity.from_dict(record.receiving_allocation["identity"])
    revision = _retained_revision(record, original)
    if revision is None:
        raise ValueError("receiving current-location revision is missing")
    expected = revision["destination"] if revision["phase"] == "complete" else revision["source"]
    if record.worktree_path != expected:
        raise ValueError("receiving record rolled back its current-location revision")
    from .receiving_creation_journal import concluded_identity

    row = _original_row(ledger.parent.parent / "workspace-creation.sqlite3", admission.key)
    if concluded_identity(row, admission) is None:
        raise ValueError("receiving original creation conclusion is missing")
    receiving._require_retained_finality(record, row, admission)


def _original_row(path: Path, key: str) -> dict:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode) or path.is_symlink()
        or getattr(before, "st_file_attributes", 0) & 0x400
        or before.st_size > 16 * 1024 * 1024
        or any(os.path.lexists(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm"))
    ):
        raise ValueError("receiving proof journal is redirected, busy or unsupported")
    with path.open("rb") as stream:
        header = stream.read(20)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
        raise ValueError("receiving proof requires a quiescent rollback-journal snapshot")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
    except sqlite3.Error as exc:
        raise ValueError("receiving original proof is unavailable or unsupported") from exc
    try:
        try:
            rows = connection.execute(
                "SELECT version, facts, status, worktree FROM creations WHERE key_digest=? LIMIT 2",
                (hashlib.sha256(key.encode()).hexdigest(),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ValueError("receiving original proof is unavailable or unsupported") from exc
    finally:
        connection.close()
    after = path.lstat()
    if (
        not os.path.samestat(before, after)
        or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        or any(os.path.lexists(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm"))
    ):
        raise ValueError("receiving proof changed during passive inspection")
    from . import creation_requests

    if len(rows) != 1:
        raise ValueError("receiving original creation operation is missing or ambiguous")
    row = rows[0]
    if (
        row[0] != creation_requests.SCHEMA_VERSION or row[2] != "terminal"
        or any(not isinstance(value, str) or len(value.encode()) > 64 * 1024 for value in (row[1], row[3]))
    ):
        raise ValueError("receiving relocation requires the original concluded creation operation")
    facts, worktree = json.loads(row[1]), json.loads(row[3])
    if not isinstance(facts, dict) or not isinstance(worktree, dict):
        raise ValueError("receiving original creation proof is malformed")
    return {"facts": facts, "status": row[2], "worktree": worktree}


def _settled_origin(record, admission, original: allocation.AllocationIdentity) -> dict:
    from . import claim_handoffs
    from .checkout_relocation_plan import read_record

    machine, project, worktree_id = allocation.qualified_ref(admission.origin)
    if (
        (machine, project) != (admission.target["machine"], admission.target["project"])
        or worktree_id == record.worktree_id
    ):
        raise ValueError("receiving relocation requires locally retained same-project origin settlement")
    origin = read_record(record.yaml_path.with_name(worktree_id + ".yaml"))
    if (origin.repo, origin.worktree_id) != (project, worktree_id):
        raise ValueError("receiving origin settlement belongs to another creator")
    matches = [
        claim for claim in origin.resources
        if isinstance(claim.producer, dict) and (
            claim.producer.get("token") == admission.token
            or claim.producer.get("request_id") == admission.request_id
        )
    ]
    if len(matches) != 1:
        raise ValueError("receiving origin settlement is missing or ambiguous")
    claim = matches[0]
    metadata = claim.producer
    if (
        claim.kind != "worktree" or claim.state != "released" or claim.metadata_opaque
        or not allocation.valid_metadata(metadata) or metadata["phase"] != "closed"
        or metadata["creator"] != admission.origin or metadata["token"] != admission.token
        or metadata["request_id"] != admission.request_id
        or metadata.get("identity") != original.to_dict() or claim.ref != original.ref
        or producer.canonical_json(metadata["recovery"]) != producer.canonical_json(admission.target)
        or any(item.metadata_opaque for item in origin.resources)
        or claim.handoff_bundle
        or claim_handoffs.active_bundle_for_claim(admission.origin, claim.ref)
    ):
        raise ValueError("receiving origin responsibility is not exactly settled")
    return metadata["closeout"]


def inspect(record, config) -> dict:
    """Read immutable original proof; never recover metadata, indices or tails."""
    from . import receiving_allocation as receiving, receiving_creation_journal as creation

    admission = receiving.parsed_receipt(record)
    admission.require_current(config)
    original = allocation.AllocationIdentity.from_dict(record.receiving_allocation["identity"])
    row = _original_row(record.yaml_path.parent.parent / "workspace-creation.sqlite3", admission.key)
    concluded = creation.concluded_identity(row, admission)
    if concluded != (original, "finalized") or record.status != "finalized":
        raise ValueError("receiving relocation requires unchanged finalized original child proof")
    revision = _retained_revision(record, original)
    if revision is not None:
        return revision["receiving_location"]
    return {
        "version": 1, "original_identity": original.to_dict(),
        "receipt_sha256": _receipt_digest(record),
        "origin_closeout": _settled_origin(record, admission, original),
    }


@contextmanager
def guard(ledger: Path, proof: dict):
    """Serialize with the original receiving operation, never create a new one."""
    from . import receiving_allocation as receiving, tracking

    original = proof["origin_closeout"]
    admission = receiving.ReceivingAllocation.from_fields({
        key: original[key] for key in ("origin", "request_id", "token", "target")
    })
    digest = hashlib.sha256(admission.key.encode()).hexdigest()
    path = ledger.parent.parent / "creation-operations" / f"{digest}.yaml"
    with tracking._RecordLock(path, blocking=False, require_sidecar=True) as operation:
        if not operation.acquired:
            raise ValueError("the original receiving creation owner is active or unobservable")
        yield
