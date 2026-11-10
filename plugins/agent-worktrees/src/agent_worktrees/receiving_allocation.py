"""Receipt-backed foreign creators; claim acknowledgement never gates child execution."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import yaml
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from . import owned_worktree as allocation, resource_producer as producer
from .resource_relationship import valid_request_id

if TYPE_CHECKING:
    from . import config as cfg, tracking

_writing: ContextVar[frozenset[str]] = ContextVar("receiving_allocation_writes", default=frozenset())


@dataclass(frozen=True)
class ReceivingAllocation:
    origin: str
    request_id: str
    token: str
    target: dict

    @classmethod
    def from_fields(cls, value: object) -> ReceivingAllocation:
        if not isinstance(value, dict) or set(value) != {"origin", "request_id", "token", "target"}:
            raise ValueError("invalid receiving allocation fields")
        allocation.qualified_ref(value["origin"])
        if not valid_request_id(value["request_id"]) or not producer.is_valid_token(value["token"]):
            raise ValueError("invalid receiving allocation request identity")
        if not allocation.valid_target(value["target"]):
            raise ValueError("invalid receiving allocation target")
        allocation.require_lifecycle_capacity(
            value["origin"], value["request_id"], value["token"], value["target"],
        )
        return cls(value["origin"], value["request_id"], value["token"], copy.deepcopy(value["target"]))

    def to_fields(self) -> dict:
        return {
            "origin": self.origin, "request_id": self.request_id, "token": self.token,
            "target": copy.deepcopy(self.target),
        }

    @property
    def key(self) -> str:
        # A changed token is a conflict under the same logical request, not
        # permission to create another child.
        logical = producer.canonical_json([self.origin, self.request_id])
        return "owned-worktree-v1:" + hashlib.sha256(logical.encode("ascii")).hexdigest()

    def require_current(self, config: cfg.Config) -> None:
        from .execution_spaces import current_execution_context

        self.from_fields(self.to_fields())
        context = current_execution_context(config)
        if (
            self.target["machine"] != context.execution_machine
            or self.target["project"] != config.repo_name
        ):
            raise ValueError("receiving allocation does not select this execution space/project")

    def receipt(self, record: tracking.WorktreeRecord) -> dict:
        return {
            "schema": allocation.SCHEMA, "version": allocation.VERSION,
            **self.to_fields(), "identity": record_identity(record).to_dict(), "phase": "created",
        }

    def require_planned_identity(
        self, worktree_id: str, creation_nonce: str, path: str, branch: str,
    ) -> None:
        allocation.AllocationIdentity.from_dict({
            "ref": f"{self.target['machine']}/{self.target['project']}/{worktree_id}",
            "creation_nonce": creation_nonce, "path": path, "branch": branch,
        })


def record_identity(record: tracking.WorktreeRecord) -> allocation.AllocationIdentity:
    from . import tracking

    return allocation.AllocationIdentity.from_dict({
        "ref": tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
        "creation_nonce": record.creation_nonce,
        "path": record.worktree_path, "branch": record.branch,
    })


def parsed_receipt(record: tracking.WorktreeRecord) -> ReceivingAllocation:
    value = record.receiving_allocation
    if not record.receiving_allocation_present or not isinstance(value, dict) or set(value) != {
        "schema", "version", "origin", "request_id", "token", "target", "identity", "phase",
    }:
        raise ValueError("missing or malformed receiving allocation receipt")
    if (
        value["schema"] != allocation.SCHEMA or type(value["version"]) is not int
        or value["version"] != allocation.VERSION or value["phase"] not in ("created", "linked")
    ):
        raise ValueError("unsupported receiving allocation receipt")
    admission = ReceivingAllocation.from_fields({
        key: value[key] for key in ("origin", "request_id", "token", "target")
    })
    identity = record_identity(record)
    if (
        value["identity"] != identity.to_dict() or record.owner_ref != admission.origin
        or allocation.qualified_ref(identity.ref)[:2] != (
            admission.target["machine"], admission.target["project"],
        )
    ):
        raise ValueError("receiving allocation receipt does not match the child/creator")
    return admission


def require_record(record: tracking.WorktreeRecord, config: cfg.Config) -> None:
    """Prove the local creation journal; no origin connection or linked phase needed."""
    from . import creation_requests

    admission = parsed_receipt(record)
    admission.require_current(config)
    row = creation_requests.inspect(config, admission.key)
    if row is None or row["status"] not in {"pending", "complete", "terminal"}:
        raise ValueError("receiving allocation has no retained creation operation")
    if producer.canonical_json(row["facts"].get("receiving_allocation")) != producer.canonical_json(admission.to_fields()):
        raise ValueError("receiving allocation conflicts with its original creation facts")
    saved = row["worktree"]
    if row["status"] in {"complete", "terminal"}:
        if not isinstance(saved, dict) or saved.get("creation_nonce") != record.creation_nonce:
            raise ValueError("receiving allocation has a different creation nonce")
        saved = saved.get("worktree")
    elif not isinstance(saved, dict) or saved.get("creation_nonce") != record.creation_nonce:
        raise ValueError("receiving allocation was not reserved before creation")
    if not isinstance(saved, dict) or any(
        saved.get(key) != value for key, value in {
            "id": record.worktree_id, "repo": record.repo,
            "branch": record.branch, "path": record.worktree_path,
        }.items()
    ):
        raise ValueError("receiving allocation conflicts with its journaled child")
    _require_retained_finality(record, row, admission)


def _require_retained_finality(
    record: tracking.WorktreeRecord, row: dict, admission: ReceivingAllocation,
) -> None:
    from .receiving_creation_journal import concluded_identity

    concluded = concluded_identity(row, admission)
    if concluded is None:
        return
    identity, status = concluded
    if (
        identity != record_identity(record)
        or record.status not in {"finalized", "archived"}
        or status == "archived" and record.status != "archived"
    ):
        raise ValueError("receiving allocation is concluded; reopening or downgrading requires new ownership")


def recover_missing(record: tracking.WorktreeRecord, records: Path) -> bool:
    """Old writers may omit a field, but cannot create journal-backed provenance."""
    from . import creation_requests

    if record.receiving_allocation_present or not record.owner_ref or not record.creation_nonce:
        return False
    path = records.parent / "workspace-creation.sqlite3"
    if not path.exists():
        return False
    if (
        record.yaml_path.is_symlink()
        or record.yaml_path.resolve() != (records / f"{record.worktree_id}.yaml").resolve()
    ):
        raise ValueError("receiving recovery ledger is redirected")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        try:
            if not any(
                row[1] == creation_requests.RECEIVING_IDENTITY_INDEX
                for row in connection.execute("PRAGMA index_list(creations)")
            ):
                # Legacy journals are upgraded once, never scanned on every
                # ordinary record save. New creation writers install the index.
                upgrade = creation_requests._connection(path)
                upgrade.close()
            rows = connection.execute(
                "SELECT key_digest, version, facts, status, worktree FROM creations "
                f"INDEXED BY {creation_requests.RECEIVING_IDENTITY_INDEX} "
                "WHERE json_extract(facts, '$.receiving_allocation.origin')=? "
                "AND json_extract(worktree, '$.creation_nonce')=?",
                (record.owner_ref, record.creation_nonce),
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"cannot recover original receiving operation: {exc}") from exc
    finally:
        connection.close()
    matches = []
    for digest, version, raw_facts, status, raw_saved in rows:
        if version != creation_requests.SCHEMA_VERSION or status not in {"pending", "complete", "terminal"}:
            raise ValueError("unsupported receiving recovery operation")
        facts, saved = json.loads(raw_facts), json.loads(raw_saved)
        child = saved if status == "pending" else saved.get("worktree")
        if not isinstance(child, dict) or any(
            child.get(key) != value for key, value in {
                "id": record.worktree_id, "repo": record.repo,
                "branch": record.branch, "path": record.worktree_path,
            }.items()
        ):
            continue
        admission = ReceivingAllocation.from_fields(facts["receiving_allocation"])
        if digest != hashlib.sha256(admission.key.encode()).hexdigest():
            raise ValueError("receiving recovery facts are not the original journal key")
        _require_retained_finality(record, {"facts": facts, "status": status, "worktree": saved}, admission)
        receipt = admission.receipt(record)
        receipt["phase"] = saved.get("receiving_phase", "created")
        matches.append(receipt)
    if not matches:
        return False
    if len(matches) != 1:
        raise ValueError("ambiguous original receiving operation")
    record.receiving_allocation = matches[0]
    record.receiving_allocation_present = True
    parsed_receipt(record)
    return True


@contextmanager
def transaction(path: Path) -> Iterator[None]:
    """Confine first-save/ack receipt changes to the receiving owner."""
    token = _writing.set(_writing.get() | {str(path.resolve())})
    try:
        yield
    finally:
        _writing.reset(token)


def preserve(
    record: tracking.WorktreeRecord, current: tracking.WorktreeRecord | None, path: Path,
) -> None:
    """Generic and stale writers cannot forge, erase or roll back receiving custody."""
    if current is not None and not current.receiving_allocation_present:
        recover_missing(current, path.parent)
    authorized = str(path.resolve()) in _writing.get()
    if current is not None and current.receiving_allocation_present:
        if current.status in {"finalized", "archived"} and (
            record.status not in {"finalized", "archived"}
            or current.status == "archived" and record.status != "archived"
        ):
            raise ValueError("receiving allocation is concluded; reopening requires new ownership")
        if authorized:
            parsed_receipt(current)
            parsed_receipt(record)
            old = current.receiving_allocation
            new = record.receiving_allocation
            if (
                not isinstance(old, dict)
                or {key: value for key, value in old.items() if key != "phase"}
                != {key: value for key, value in new.items() if key != "phase"}
                or old["phase"] not in ("created", "linked")
                or old["phase"] == "linked" and new["phase"] != "linked"
            ):
                raise ValueError("receiving allocation identity is immutable")
        else:
            record.receiving_allocation = copy.deepcopy(current.receiving_allocation)
            record.receiving_allocation_present = True
            record.owner_ref = current.owner_ref
            record.creation_nonce = current.creation_nonce
        try:
            admission = parsed_receipt(current)
        except ValueError:
            # Generic writes retain malformed bytes; they never grant mutation
            # authority, which still requires the complete parsed receipt.
            return
        from . import creation_requests

        row = creation_requests.inspect_journal(
            path.parent.parent / "workspace-creation.sqlite3", admission.key,
        )
        if row is not None:
            _require_retained_finality(record, row, admission)
        return
    if record.receiving_allocation_present:
        if current is not None or not authorized:
            raise ValueError("receiving allocation requires the original creation transaction")
        parsed_receipt(record)


def persist_conclusion(record: tracking.WorktreeRecord) -> None:
    """Run after the actual record save, retaining finality in its original journal."""
    if record.receiving_allocation_present and record.status in {"finalized", "archived"}:
        from .execution_spaces import project_record_config
        from .receiving_creation_journal import note_conclusion

        note_conclusion(record, project_record_config(record))


def serialized_receipt(record: tracking.WorktreeRecord) -> str:
    """Keep unknown receipt bytes structurally visible, including malformed values."""
    return yaml.safe_dump(
        {"receiving_allocation": record.receiving_allocation},
        default_flow_style=False, sort_keys=False,
    ) if record.receiving_allocation_present else ""
