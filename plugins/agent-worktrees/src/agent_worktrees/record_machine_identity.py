"""Tracking identity roles and immutable identity fences for versioned records."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from .worktree_machine_identity import WorktreeMachineIdentity, WorktreeMachineIdentityError


class IdentityRecord(Protocol):
    machine: str
    platform: str
    _loaded_record_subject: tuple[object, ...] | None


class RecordMachineIdentityView(IdentityRecord):
    """Compatibility projections of transient reporter naming facts."""

    @property
    def execution_machine(self) -> str:
        return execution_machine(self)

    @property
    def machine_fields(self) -> dict[str, object]:
        return record_fields(self)

    @property
    def execution_reference_fields(self) -> dict[str, object]:
        result: dict[str, object] = {"machine": execution_machine(self)}
        if machine_identity(self) is not None:
            result["worktree_identity"] = record_fields(self)
        return result


def machine_identity(record: IdentityRecord) -> WorktreeMachineIdentity | None:
    identity = getattr(record, "machine_identity", None)
    if identity is None:
        return None
    if (
        not isinstance(identity, WorktreeMachineIdentity)
        or identity.machine != record.machine
        or identity.environment != record.platform
    ):
        raise WorktreeMachineIdentityError("record machine identity conflicts with machine/platform")
    return identity


def execution_machine(record: IdentityRecord) -> str:
    """One-term reporter key, not an identity read from tracking YAML."""
    identity = machine_identity(record)
    return identity.execution_machine if identity is not None else record.machine


def record_fields(record: IdentityRecord) -> dict[str, object]:
    identity = machine_identity(record)
    result = identity.to_record_fields() if identity is not None else {
        "machine": record.machine, "platform": record.platform,
    }
    if getattr(getattr(record, "_reporting_context", None), "unavailable", False):
        result["machine_context"] = {"status": "unavailable", "reason": "invalid-machine-registry"}
    return result


def identity_fence(record: IdentityRecord) -> tuple[object, ...]:
    return tuple(record_fields(record).items())


def subject_fence(record: IdentityRecord, path: Path | None) -> tuple[object, ...]:
    """Identify the receiving ledger and allocation independently of its labels."""
    ledger = None if path is None else os.path.normcase(os.path.abspath(str(path)))
    return (ledger, *(getattr(record, name, None) or "" for name in (
        "repo", "worktree_id", "worktree_path", "branch", "started_at",
    )))


def remember_loaded_subject(record: IdentityRecord, path: Path) -> None:
    """Retain transient save evidence without adding persisted identity fields."""
    record._loaded_record_subject = subject_fence(record, path)


def require_unchanged_identity(record: IdentityRecord, current: IdentityRecord | None) -> None:
    """Fence the receiving subject independently of changing reporter labels."""
    if current is not None:
        path = getattr(current, "_loaded_from", None)
        expected_subject = getattr(record, "_loaded_record_subject", None)
        if expected_subject is None:
            expected_subject = subject_fence(record, path)
        if subject_fence(current, path) != expected_subject:
            raise WorktreeMachineIdentityError("record subject changed; reload the receiving record")
