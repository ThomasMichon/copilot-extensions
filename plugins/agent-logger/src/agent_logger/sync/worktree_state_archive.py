"""Additive local publication of owner-verified exports, never source retirement."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from agent_procutil.owned_budget import OwnedExecutionBudget

from agent_logger.aggregate import ResolvedPlan
from agent_logger.config import Config
from agent_logger.sync.publication_directory import PublicationDirectory, publication_directory
from agent_logger.sync.targets.base import Target
from agent_logger.sync.worktree_state_namespace import claim_namespace, has_namespace
from agent_logger.sync.worktree_state import (
    MAX_MANIFEST_BYTES, MAX_MEMBER_BYTES, ExportVerifier, WorktreeStateError,
    WorktreeStateReceipt, WorktreeStateRevision, admit_collection, canonical_bytes,
    contributing_sessions, decode_object,
)
from agent_logger.sync.worktree_state_contributors import (
    MAX_CONTRIBUTOR_BYTES, ContributorReader, contributor_bytes,
)

_METADATA_BYTES = 32768
_COPY_BYTES = 1 << 20
_DEADLINE_SECONDS = 120


def _read(directory: PublicationDirectory, name: str, limit: int) -> bytes:
    with directory.open(name, protected=True) as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise WorktreeStateError("collection metadata is empty or exceeds its bound")
    return raw


def _write(directory: PublicationDirectory, name: str, raw: bytes) -> None:
    if directory.fd is None:
        raise WorktreeStateError("typed collection requires directory-relative durable publication")
    with directory.create(name, mode=0o600) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory.fd)


def _verified_manifest(
    directory: PublicationDirectory, identity: WorktreeStateRevision, verify: ExportVerifier,
) -> tuple[dict[str, Any], bytes]:
    raw = _read(directory, "manifest.json", MAX_MANIFEST_BYTES)
    manifest = decode_object(raw)
    verified = verify(
        directory.path, worktree=identity.worktree, store_id=identity.store_id,
        expected_revision=identity.revision,
    )
    if (
        manifest != verified or manifest.get("schema") != "efforts.agenda-export"
        or type(manifest.get("version")) is not int or manifest["version"] != 1
        or manifest.get("completeness") != "complete"
        or manifest.get("worktree") != identity.worktree
        or manifest.get("store_id") != identity.store_id
        or type(manifest.get("revision")) is not int
        or manifest["revision"] != identity.revision
        or set(manifest.get("members", {})) != {"agenda.sqlite3"}
    ):
        raise WorktreeStateError("owner readback did not verify the selected complete export")
    member = manifest["members"]["agenda.sqlite3"]
    if (
        not isinstance(member, dict) or set(member) != {"bytes", "sha256"}
        or type(member["bytes"]) is not int or not 0 < member["bytes"] <= MAX_MEMBER_BYTES
        or not isinstance(member["sha256"], str) or len(member["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in member["sha256"])
        or _read(directory, "manifest.json", MAX_MANIFEST_BYTES) != raw
    ):
        raise WorktreeStateError("owner export member or manifest changed")
    return manifest, raw


def _copy_member(
    source: PublicationDirectory, destination: PublicationDirectory, member: dict[str, Any],
    budget: OwnedExecutionBudget | None = None,
) -> None:
    deadline = time.monotonic() + _DEADLINE_SECONDS
    if budget is not None:
        deadline = min(deadline, budget.execution_deadline)
    if time.monotonic() >= deadline:
        raise WorktreeStateError("complete collection exhausted its shared time budget")
    digest, count = hashlib.sha256(), 0
    with source.open("agenda.sqlite3", protected=True) as incoming:
        before = os.fstat(incoming.fileno())
        if before.st_nlink != 1 or before.st_size != member["bytes"]:
            raise WorktreeStateError("export database is aliased or changed before collection")
        with destination.create("agenda.sqlite3", mode=0o600) as outgoing:
            while chunk := incoming.read(_COPY_BYTES):
                count += len(chunk)
                if count > member["bytes"] or time.monotonic() >= deadline:
                    raise WorktreeStateError("complete collection exceeded its byte or time budget")
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = os.fstat(incoming.fileno())
    if (
        count != member["bytes"] or digest.hexdigest() != member["sha256"]
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise WorktreeStateError("export database changed during collection")


def _receipt(
    identity: WorktreeStateRevision, root: Path, manifest: dict[str, Any], raw: bytes,
    provenance: bytes | None = None,
) -> WorktreeStateReceipt:
    member = manifest["members"]["agenda.sqlite3"]
    return WorktreeStateReceipt(
        identity, str(root), hashlib.sha256(raw).hexdigest(),
        member["sha256"], member["bytes"],
        hashlib.sha256(provenance).hexdigest() if provenance is not None else None,
    )


def _intent(receipt: WorktreeStateReceipt) -> bytes:
    value = {
        "schema": "agent-logger.worktree-state-collection",
        "version": 1 if receipt.contributors_sha256 is None else 2,
        "identity": receipt.identity.as_dict(), "destination": receipt.destination,
        "manifest_sha256": receipt.manifest_sha256,
    }
    if receipt.contributors_sha256 is not None:
        value["contributors_sha256"] = receipt.contributors_sha256
    return canonical_bytes(value)


def _provenance(
    directory: PublicationDirectory, identity: WorktreeStateRevision,
    manifest: bytes, read: ContributorReader | None,
) -> bytes | None:
    if read is None:
        return None
    raw = contributor_bytes(directory.path, identity, manifest, read)
    if _read(directory, "manifest.json", MAX_MANIFEST_BYTES) != manifest:
        raise WorktreeStateError("manifest changed during contributor verification")
    return raw


def _references(directory: Path, receipt: WorktreeStateReceipt, sessions: tuple[str, ...]) -> None:
    with publication_directory(directory) as references:
        for session in sessions:
            name = hashlib.sha256(session.encode("utf-8")).hexdigest() + ".json"
            raw = canonical_bytes({
                "schema": "agent-logger.worktree-state-reference", "version": 1,
                "session": session, "identity": receipt.identity.as_dict(),
                "manifest_sha256": receipt.manifest_sha256,
            })
            try:
                _write(references, name, raw)
            except FileExistsError:
                if _read(references, name, _METADATA_BYTES) != raw:
                    raise WorktreeStateError("existing contributing-session reference conflicts")


def _readback(
    directory: Path, identity: WorktreeStateRevision, root: Path, verify: ExportVerifier,
    contributors: ContributorReader | None = None,
) -> WorktreeStateReceipt:
    if not has_namespace(root):
        raise WorktreeStateError("typed collection namespace is missing or unowned")
    with publication_directory(directory, create=False) as revision:
        with publication_directory(directory / "export", create=False) as exported:
            manifest, raw = _verified_manifest(exported, identity, verify)
            provenance = _provenance(exported, identity, raw, contributors)
        if provenance is not None and _read(
            revision, "contributors.json", MAX_CONTRIBUTOR_BYTES,
        ) != provenance:
            raise WorktreeStateError("retained contributor provenance differs from owner readback")
        receipt = _receipt(identity, root, manifest, raw, provenance)
        if (
            _read(revision, "collection.json", _METADATA_BYTES) != _intent(receipt)
            or _read(revision, "receipt.json", _METADATA_BYTES) != canonical_bytes(receipt.as_dict())
        ):
            raise WorktreeStateError("stored receipt does not bind the actual destination and export")
        return receipt


def readback_worktree_state(
    cfg: Config, target: Target, plan: ResolvedPlan, identity: WorktreeStateRevision,
    verify: ExportVerifier,
    *, contributors: ContributorReader | None = None,
) -> WorktreeStateReceipt:
    """Verify actual retained bytes afresh; a persisted receipt alone is not proof."""
    root = admit_collection(cfg, target, plan, identity)
    return _readback(root / identity.relative_directory, identity, root, verify, contributors)


def collect_worktree_state(
    cfg: Config, target: Target, plan: ResolvedPlan, source: Path,
    identity: WorktreeStateRevision, sessions: tuple[str, ...], verify: ExportVerifier,
    *, contributors: ContributorReader | None = None, budget: OwnedExecutionBudget | None = None,
) -> WorktreeStateReceipt:
    """Collect one explicitly selected export under the existing target/policy.

    The caller owns provider selection and complete export. This boundary never
    locates live state, starts a provider, redacts the ledger, schedules work,
    replaces a prior revision or deletes partial/retained evidence.
    """
    if sessions != () or contributors is None:
        sessions = contributing_sessions(sessions)
    root = admit_collection(cfg, target, plan, identity)
    destination = root / identity.relative_directory
    source = source.expanduser().absolute()
    if source == root or source in root.parents or root in source.parents:
        raise WorktreeStateError("export source and collection destination must be disjoint")
    with publication_directory(source, create=False) as exported:
        manifest, raw = _verified_manifest(exported, identity, verify)
        provenance = _provenance(exported, identity, raw, contributors)
        receipt = _receipt(identity, root, manifest, raw, provenance)
        intent = _intent(receipt)
        try:
            claim_namespace(root)
        except ValueError as error:
            raise WorktreeStateError(str(error)) from error
        with publication_directory(destination) as revision:
            names = revision.names()
            if not names:
                try:
                    _write(revision, "collection.json", intent)
                    publisher = True
                except FileExistsError:
                    publisher = False
            else:
                publisher = False
            if not publisher:
                if _read(revision, "collection.json", _METADATA_BYTES) != intent:
                    raise WorktreeStateError("existing worktree revision conflicts; nothing overwritten")
                try:
                    existing = _readback(destination, identity, root, verify, contributors)
                except (OSError, ValueError) as error:
                    raise WorktreeStateError(
                        "prior collection is incomplete or changed; retained for owner reconciliation"
                    ) from error
                if existing != receipt:
                    raise WorktreeStateError("same worktree revision has different exported bytes")
            else:
                with publication_directory(destination / "export") as copied:
                    if copied.names():
                        raise WorktreeStateError("new collection export directory is not empty")
                    _copy_member(exported, copied, manifest["members"]["agenda.sqlite3"], budget)
                    _write(copied, "manifest.json", raw)
                    actual, actual_raw = _verified_manifest(copied, identity, verify)
                    if actual != manifest or actual_raw != raw:
                        raise WorktreeStateError("collected export differs from its verified source")
                    if _provenance(copied, identity, actual_raw, contributors) != provenance:
                        raise WorktreeStateError("collected contributor provenance differs from source")
                if provenance is not None:
                    _write(revision, "contributors.json", provenance)
                _write(revision, "receipt.json", canonical_bytes(receipt.as_dict()))
            _references(destination / "references", receipt, sessions)
    return readback_worktree_state(
        cfg, target, plan, identity, verify, contributors=contributors,
    )
