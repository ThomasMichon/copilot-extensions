"""Replay-safe allocation intent, qualified by the owner's project state."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from . import config as cfg, creation_allocation, tracking

SCHEMA_VERSION = 1


class CreationRequestFailure(RuntimeError):
    def __init__(self, status: str, message: str, worktree: dict | None = None):
        self.status = status
        self.worktree = worktree
        super().__init__(message)

    def payload(self) -> dict:
        return {
            "error": str(self),
            "creation": {
                "version": SCHEMA_VERSION, "status": self.status, "repeat_new": False,
            },
            **({"worktree": self.worktree} if self.worktree is not None else {}),
        }


def _canonical_path(value: str) -> str:
    return os.path.normcase(os.path.realpath(value))


def allocation_facts(config: cfg.Config, **options) -> dict:
    repo = config.default_repo
    kind = options["kind"]
    owner_ref = options["owner_ref"]
    parsed_owner = tracking.parse_claim_ref(owner_ref) if owner_ref else None
    if parsed_owner is not None and parsed_owner.is_qualified:
        owner_ref = tracking.format_claim_ref(
            parsed_owner.machine, parsed_owner.project, parsed_owner.worktree_id,
        )
    return {
        "project": config.repo_name,
        "anchor": _canonical_path(repo.anchor),
        "worktree_root": _canonical_path(repo.worktree_root),
        "remote": repo.remote,
        "default_branch": repo.default_branch,
        "kind": kind,
        "interface": options["interface"] or ("acp" if kind == "bridge" else "cli"),
        "origin": options["origin"] or (
            "system" if kind == "system"
            else "delegate" if kind == "bridge" and options.get("caller_worktree")
            else "user"
        ),
        "owner": options["owner"],
        "owner_ref": owner_ref,
        "name": options["name"] if kind == "system" else None,
        "bound_agent": (options["bound_agent"] or "").strip() or None,
        "no_pair": options["no_pair"] or bool(os.environ.get("AGENT_WORKTREES_NO_PAIR")),
        "seed_sha256": hashlib.sha256((options["pending_seed"] or "").encode()).hexdigest(),
        "pending_seed_id": options.get("pending_seed_id"),
        "allocation": (
            options["admitted_allocation"].to_fields() if options.get("admitted_allocation") is not None
            else options.get("allocation")
        ),
        # Attempt provenance is evidence about the original allocation, not a
        # logical revision authorizing another one.
        "dispatch_task_id": (options["dispatch_attempt"] or {}).get("task_id"),
    }


def _connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS creations ("
        "key_digest TEXT PRIMARY KEY, version INTEGER NOT NULL, facts TEXT NOT NULL, "
        "status TEXT NOT NULL, worktree TEXT)"
    )
    connection.commit()
    return connection


def _owner_record(worktree: dict, records: Path, project: str) -> tracking.WorktreeRecord:
    identity_fields = ("id", "repo", "branch", "path")
    if (
        any(not isinstance(worktree.get(field), str) or not worktree[field] for field in identity_fields)
        or Path(worktree["id"]).name != worktree["id"]
        or "/" in worktree["id"] or "\\" in worktree["id"]
        or worktree["repo"] != project
    ):
        raise CreationRequestFailure("conflict", "Invalid owner-returned allocation identity", worktree)
    record_path = records / f"{worktree['id']}.yaml"
    try:
        record = tracking.load_record(record_path)
    except FileNotFoundError:
        raise CreationRequestFailure(
            "terminal", "The original allocation is no longer tracked", worktree,
        ) from None
    try:
        checkout = Path(record.worktree_path).stat()
    except FileNotFoundError:
        raise CreationRequestFailure(
            "terminal", "The original allocation was removed", worktree,
        ) from None
    if record.status in ("finalized", "archived") or not stat.S_ISDIR(checkout.st_mode):
        raise CreationRequestFailure("terminal", "The original allocation was removed or retired", worktree)
    if (
        record.worktree_id != worktree["id"]
        or record.repo != worktree["repo"]
        or record.branch != worktree["branch"]
        or _canonical_path(record.worktree_path) != _canonical_path(worktree["path"])
    ):
        raise CreationRequestFailure("conflict", "The original allocation's owner identity changed", worktree)
    if not record.creation_nonce:
        raise CreationRequestFailure("pending", "The allocation has no authoritative creation nonce", worktree)
    try:
        creation_allocation.require_result_identity(record, worktree)
    except ValueError as exc:
        raise CreationRequestFailure("conflict", str(exc), worktree) from exc
    return record


def _replay(
    row: tuple, facts: str, records: Path, project: str,
    validate_record: Callable[[tracking.WorktreeRecord], None] | None = None,
) -> dict:
    version, saved_facts, status, saved_worktree = row
    if version != SCHEMA_VERSION:
        raise CreationRequestFailure("unsupported", "Unsupported creation journal version")
    if saved_facts != facts:
        raise CreationRequestFailure("conflict", "Creation key is already bound to different allocation facts")
    receipt = json.loads(saved_worktree) if saved_worktree is not None else None
    if status == "pending":
        raise CreationRequestFailure(
            "pending", "Creation is pending or interrupted; reconcile the original allocation, never create again",
            receipt,
        )
    if status == "terminal":
        raise CreationRequestFailure(
            "terminal", "The original allocation is permanently terminal",
            receipt.get("worktree") if isinstance(receipt, dict) else None,
        )
    if status != "complete" or not isinstance(receipt, dict):
        raise CreationRequestFailure("invalid", "Invalid creation journal state")
    worktree = receipt.get("worktree")
    if not isinstance(worktree, dict):
        raise CreationRequestFailure("invalid", "Invalid creation result")
    record = _owner_record(worktree, records, project)
    if record.creation_nonce != receipt.get("creation_nonce"):
        raise CreationRequestFailure("conflict", "The original allocation was replaced", worktree)
    if "record_identity" in receipt:
        try:
            creation_allocation.require_record_evidence(record, receipt["record_identity"])
        except ValueError as exc:
            raise CreationRequestFailure("conflict", str(exc), worktree) from exc
    if validate_record is not None:
        validate_record(record)
    return {
        "worktree": worktree,
        "creation": {"version": SCHEMA_VERSION, "status": "complete", "replayed": True},
    }


def create(
    config: cfg.Config,
    key: str,
    facts: dict,
    allocate: Callable[[Callable[[dict], None]], dict],
    *,
    validate_record: Callable[[tracking.WorktreeRecord], None] | None = None,
) -> dict:
    """Persist intent before effects; only the insert winner may allocate."""
    if not isinstance(key, str) or not key.strip() or len(key) > 1024:
        raise CreationRequestFailure("invalid", "Creation key must be non-empty and at most 1024 characters")
    records = cfg.tracking_dir(config.repo_name)
    path = records.parent / "workspace-creation.sqlite3"
    digest = hashlib.sha256(key.encode()).hexdigest()
    encoded_facts = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    connection = _connection(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version, facts, status, worktree FROM creations WHERE key_digest=?",
                (digest,),
            ).fetchone()
            retry_refused = (
                row is not None and row[0] == SCHEMA_VERSION and row[1] == encoded_facts
                and row[2] == "refused" and row[3] is None
            )
            if row is not None and not retry_refused:
                try:
                    return _replay(row, encoded_facts, records, config.repo_name, validate_record)
                except CreationRequestFailure as exc:
                    if exc.status == "terminal":
                        connection.execute(
                            "UPDATE creations SET status='terminal' WHERE key_digest=?",
                            (digest,),
                        )
                        connection.commit()
                    raise
            if retry_refused:
                connection.execute(
                    "UPDATE creations SET status='pending' WHERE key_digest=?",
                    (digest,),
                )
            else:
                connection.execute(
                    "INSERT INTO creations VALUES (?, ?, ?, 'pending', NULL)",
                    (digest, SCHEMA_VERSION, encoded_facts),
                )

        def reserve(worktree: dict) -> None:
            with connection:
                changed = connection.execute(
                    "UPDATE creations SET worktree=? WHERE key_digest=? AND status='pending' "
                    "AND worktree IS NULL",
                    (json.dumps(worktree, sort_keys=True), digest),
                ).rowcount
                if changed != 1:
                    raise CreationRequestFailure("conflict", "Creation intent already has an allocation identity")

        try:
            result = allocate(reserve)
        except (RuntimeError, ValueError, OSError):
            with connection:
                connection.execute(
                    "UPDATE creations SET status='refused' WHERE key_digest=? "
                    "AND status='pending' AND worktree IS NULL",
                    (digest,),
                )
            raise
        worktree = result["worktree"]
        record = _owner_record(worktree, records, config.repo_name)
        if validate_record is not None:
            validate_record(record)
        with connection:
            saved_identity = connection.execute(
                "SELECT worktree FROM creations WHERE key_digest=? AND status='pending'",
                (digest,),
            ).fetchone()
            if saved_identity is None or saved_identity[0] is None:
                raise CreationRequestFailure("conflict", "Creation completed without a reserved allocation identity")
            reserved = json.loads(saved_identity[0])
            if any(reserved.get(field) != worktree.get(field) for field in ("id", "repo", "branch", "path")):
                raise CreationRequestFailure("conflict", "Creation returned a different allocation identity", reserved)
            changed = connection.execute(
                "UPDATE creations SET status='complete', worktree=? "
                "WHERE key_digest=? AND status='pending'",
                (json.dumps({
                    "worktree": worktree, "creation_nonce": record.creation_nonce,
                    "record_identity": creation_allocation.record_evidence(record),
                }, sort_keys=True), digest),
            ).rowcount
            if changed != 1:
                raise CreationRequestFailure("conflict", "Creation intent changed before completion")
        return {
            "worktree": worktree,
            "creation": {"version": SCHEMA_VERSION, "status": "complete", "replayed": False},
        }
    finally:
        connection.close()
