"""Tracking identity roles and immutable identity fences for versioned records."""

from __future__ import annotations

from typing import Protocol

import yaml

from .worktree_machine_identity import WorktreeMachineIdentity, WorktreeMachineIdentityError


class IdentityRecord(Protocol):
    machine: str
    platform: str


class RecordMachineIdentityView(IdentityRecord):
    """Derived identity roles; the tracking dataclass owns the stored fields."""

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
    """One-term owner key; historical records retain their exact original label."""
    identity = machine_identity(record)
    return identity.execution_machine if identity is not None else record.machine


def record_fields(record: IdentityRecord) -> dict[str, object]:
    identity = machine_identity(record)
    return identity.to_record_fields() if identity is not None else {
        "machine": record.machine, "platform": record.platform,
    }


def identity_fence(record: IdentityRecord) -> tuple[object, ...]:
    return tuple(record_fields(record).items())


def serialized_fields(record: IdentityRecord) -> str:
    if machine_identity(record) is None:
        return f"machine: {record.machine}\nplatform: {record.platform}\n"
    return yaml.safe_dump(record_fields(record), sort_keys=False)


def require_unchanged_identity(record: IdentityRecord, current: IdentityRecord | None) -> None:
    """Allow existing explicit legacy-label repair, never implicit tuple migration."""
    expected = getattr(record, "_loaded_machine_identity", None)
    if expected is None:
        expected = identity_fence(record)
    if current is not None and identity_fence(current) != expected:
        raise WorktreeMachineIdentityError("record owner machine identity changed; reload the record")
    if current is not None and (
        machine_identity(record) is not None or machine_identity(current) is not None
    ) and identity_fence(record) != identity_fence(current):
        raise WorktreeMachineIdentityError("versioned record machine identity is immutable")
