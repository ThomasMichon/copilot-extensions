"""Receiving cancellation/conclusion in the existing workspace-creation journal."""

from __future__ import annotations

import hashlib
import json

from . import config as cfg, creation_requests, owned_worktree as allocation
from . import resource_producer as producer, tracking
from .receiving_allocation import ReceivingAllocation, parsed_receipt, record_identity


def _same_facts(facts: dict, admission: ReceivingAllocation) -> bool:
    return producer.canonical_json(facts.get("receiving_allocation")) == producer.canonical_json(admission.to_fields())


def cancel(config: cfg.Config, admission: ReceivingAllocation) -> None:
    """Seal before identity reservation; an in-flight reserve then fails before Git."""
    admission.require_current(config)
    path = cfg.tracking_dir(config.repo_name).parent / "workspace-creation.sqlite3"
    digest = hashlib.sha256(admission.key.encode()).hexdigest()
    connection = creation_requests._connection(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version, facts, status, worktree FROM creations WHERE key_digest=?", (digest,),
            ).fetchone()
            if row is not None:
                if row[0] != creation_requests.SCHEMA_VERSION or not _same_facts(json.loads(row[1]), admission):
                    raise ValueError("cancellation conflicts with original receiving operation")
                if row[2] == "terminal" and row[3] == '{"cancelled":true}':
                    return
                if row[2] not in {"pending", "refused"} or row[3] is not None:
                    raise ValueError("receiving allocation has reserved effects; cancellation is not proven safe")
                connection.execute(
                    "UPDATE creations SET status='terminal', worktree=? WHERE key_digest=?",
                    ('{"cancelled":true}', digest),
                )
            else:
                connection.execute(
                    "INSERT INTO creations VALUES (?, ?, ?, 'terminal', ?)",
                    (
                        digest, creation_requests.SCHEMA_VERSION,
                        producer.canonical_json({"receiving_allocation": admission.to_fields()}),
                        '{"cancelled":true}',
                    ),
                )
    finally:
        connection.close()


def note_conclusion(record: tracking.WorktreeRecord, config: cfg.Config) -> None:
    """Persist actual saved child finality before its directory/record may disappear."""
    if not record.receiving_allocation_present or record.status not in {"finalized", "archived"}:
        return
    admission = parsed_receipt(record)
    admission.require_current(config)
    proven = tracking.load_record(record.yaml_path)
    from .receiving_allocation import recover_missing

    recover_missing(proven, cfg.tracking_dir(config.repo_name))
    if (
        proven.status != record.status
        or record_identity(proven) != record_identity(record)
        or producer.canonical_json(parsed_receipt(proven).to_fields()) != producer.canonical_json(admission.to_fields())
    ):
        raise ValueError("receiving conclusion is not the actual saved child state")
    path = cfg.tracking_dir(config.repo_name).parent / "workspace-creation.sqlite3"
    digest = hashlib.sha256(admission.key.encode()).hexdigest()
    connection = creation_requests._connection(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version, facts, status, worktree FROM creations WHERE key_digest=?", (digest,),
            ).fetchone()
            if (
                row is None or row[0] != creation_requests.SCHEMA_VERSION
                or not _same_facts(json.loads(row[1]), admission)
                or row[2] not in {"pending", "complete", "terminal"} or row[3] is None
            ):
                raise ValueError("cannot retain conclusion without the original receiving operation")
            saved = json.loads(row[3])
            worktree = saved if row[2] == "pending" else saved.get("worktree")
            if not isinstance(worktree, dict) or any(
                worktree.get(key) != value for key, value in {
                    "id": record.worktree_id, "repo": record.repo,
                    "branch": record.branch, "path": record.worktree_path,
                }.items()
            ) or saved.get("creation_nonce") != record.creation_nonce:
                raise ValueError("cannot retain conclusion for a different receiving incarnation")
            retained = concluded_identity({
                "facts": json.loads(row[1]), "status": row[2], "worktree": saved,
            }, admission)
            if retained is not None:
                identity, status = retained
                if identity != record_identity(record):
                    raise ValueError("cannot retain conclusion for a different receiving incarnation")
                if status == "archived" and record.status != "archived":
                    raise ValueError("cannot downgrade retained archived receiving conclusion")
            receipt = {
                **({} if row[2] == "pending" else saved),
                "worktree": worktree, "creation_nonce": record.creation_nonce,
                "receiving_conclusion": {
                    "identity": record_identity(record).to_dict(), "status": record.status,
                },
            }
            connection.execute(
                "UPDATE creations SET status='terminal', worktree=? WHERE key_digest=?",
                (producer.canonical_json(receipt), digest),
            )
    finally:
        connection.close()


def note_link(record: tracking.WorktreeRecord, config: cfg.Config) -> None:
    """Retain the optional acknowledgement even if an old writer omits its field."""
    admission = parsed_receipt(record)
    admission.require_current(config)
    path = cfg.tracking_dir(config.repo_name).parent / "workspace-creation.sqlite3"
    connection = creation_requests._connection(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = creation_requests.inspect(config, admission.key)
            if (
                row is None or row["status"] != "complete"
                or not _same_facts(row["facts"], admission)
                or not isinstance(row["worktree"], dict)
                or row["worktree"].get("creation_nonce") != record.creation_nonce
                or tracking.load_record(record.yaml_path).receiving_allocation != record.receiving_allocation
                or record.receiving_allocation["phase"] != "linked"
            ):
                raise ValueError("receiving acknowledgement is not the original saved child state")
            connection.execute(
                "UPDATE creations SET worktree=? WHERE key_digest=?",
                (
                    producer.canonical_json({**row["worktree"], "receiving_phase": "linked"}),
                    hashlib.sha256(admission.key.encode()).hexdigest(),
                ),
            )
    finally:
        connection.close()


def concluded_identity(row: dict, admission: ReceivingAllocation) -> tuple[allocation.AllocationIdentity, str] | None:
    """Read only retained exact finality, never infer it from missing tracking files."""
    if row["status"] != "terminal" or not _same_facts(row["facts"], admission):
        return None
    receipt = row["worktree"]
    if not isinstance(receipt, dict) or "receiving_conclusion" not in receipt:
        return None
    conclusion = receipt["receiving_conclusion"]
    if not isinstance(conclusion, dict) or set(conclusion) != {"identity", "status"}:
        raise ValueError("invalid retained receiving conclusion")
    if conclusion["status"] not in ("finalized", "archived"):
        raise ValueError("invalid retained receiving lifecycle status")
    identity = allocation.AllocationIdentity.from_dict(conclusion["identity"])
    machine, project, worktree_id = allocation.qualified_ref(identity.ref)
    worktree = receipt.get("worktree")
    if (
        (machine, project) != (admission.target["machine"], admission.target["project"])
        or receipt.get("creation_nonce") != identity.creation_nonce
        or not isinstance(worktree, dict)
        or any(worktree.get(key) != value for key, value in {
            "id": worktree_id, "repo": project, "path": identity.path, "branch": identity.branch,
        }.items())
    ):
        raise ValueError("retained receiving conclusion conflicts with its original child")
    return identity, conclusion["status"]
