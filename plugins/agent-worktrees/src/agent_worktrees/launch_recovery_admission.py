"""Passive recovery inspection; no native, credential or admission effects."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import math
from pathlib import Path
from typing import Protocol

from agent_remote_driver.headed.recovery import RecoveryDescriptor

from . import launch_attempt, launch_execution, launch_recovery_store

_CLOSED = frozenset({"finalizing", "complete", "completed", "finalized", "archived", "orphaned"})


@dataclass(frozen=True)
class OwnerIntent:
    """Immutable original-launch snapshot, retained separately from the request."""

    creation_nonce: str
    status: str
    head_session: str | None
    lifecycle_revision: int
    head_revision: int
    execution_machine: str
    repo: str
    worktree_id: str
    worktree_path: str
    execution_leg: str | None = field(repr=False)
    session_backend: str | None = field(repr=False)

    @classmethod
    def capture(cls, record_path: Path) -> OwnerIntent:
        record, _ = launch_recovery_store._binding(record_path)
        if record.status in _CLOSED:
            raise ValueError("Original owner intent is closed")
        return cls._from_record(record)

    @classmethod
    def _from_record(cls, record) -> OwnerIntent:
        if record.execution_leg_opaque or record.session_backend_opaque:
            raise ValueError("Opaque provider state cannot establish original owner intent")
        return cls(
            record.creation_nonce, record.status, record.resolved_head_session,
            record.lifecycle_revision, record.head_revision, record.execution_machine,
            record.repo, record.worktree_id, record.worktree_path,
            json.dumps(record.execution_leg.to_dict(), sort_keys=True, allow_nan=False)
            if record.execution_leg is not None else None,
            json.dumps(record.session_backend.to_dict(), sort_keys=True, allow_nan=False)
            if record.session_backend is not None else None,
        )


@dataclass(frozen=True)
class SourceSelection:
    target: str
    account: str
    recipient: str
    route: str
    controller_generation: str

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if (
                type(value) is not str or not 1 <= len(value) <= 256
                or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
            ):
                raise ValueError("An exact bounded source selection is required")


@dataclass(frozen=True)
class RecoveryBinding:
    """Original-owner inputs, never constructed from a recovery request."""

    descriptor: RecoveryDescriptor
    correlation: launch_execution.Correlation
    launch_operation_id: str
    source: SourceSelection
    owner_intent: OwnerIntent
    native_owner: object = field(repr=False, compare=False)
    _native_scope: object | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.descriptor) is not RecoveryDescriptor
            or type(self.correlation) is not launch_execution.Correlation
            or type(self.source) is not SourceSelection
            or type(self.owner_intent) is not OwnerIntent or self.native_owner is None
        ):
            raise ValueError("An independently retained original launch binding is required")
        launch_attempt.identity(self.launch_operation_id)
        object.__setattr__(self, "_native_scope", getattr(self.native_owner, "native_scope", None))


class RemainingLease(Protocol):
    def remaining_seconds(self) -> float: ...


@dataclass(frozen=True)
class RecoveryObservation:
    """Owner-supplied evidence, not a native settlement or authority certificate."""

    binding: RecoveryBinding
    source_state: str
    authority_revision: int | None
    lease: RemainingLease | None = field(repr=False, compare=False)
    scope_settled: bool | None = None


class RecoveryAdmissionInspector:
    """Inspect one retained operation without acquiring, writing or dispatching.

    A candidate means only that supplied observations match retained inputs.
    No result is reusable as admission, current-source verification or native
    settlement. The real provider/final-boundary integration remains unwired.
    """

    def __init__(
        self, record_path: Path, binding: RecoveryBinding, operation_id: str,
    ) -> None:
        if type(binding) is not RecoveryBinding:
            raise ValueError("A retained recovery binding is required")
        if (
            type(operation_id) is not str or not 1 <= len(operation_id) <= 256
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in operation_id)
        ):
            raise ValueError("An exact original recovery operation is required")
        record, owner = launch_recovery_store._binding(record_path)
        launch_recovery_store._descriptor(binding.descriptor, owner, record.worktree_id)
        if OwnerIntent._from_record(record) != binding.owner_intent:
            raise ValueError("Original owner intent changed")
        if record.status in _CLOSED:
            raise ValueError("Original owner intent is closed")
        self._path, self._binding, self._operation = record_path, binding, operation_id
        self._owner_state = binding.owner_intent
        self._store = launch_recovery_store.WorktreeRecoveryStore(record_path)

    def _launch(self) -> dict:
        binding = self._binding
        value = launch_execution.read(
            self._path.parent, binding.correlation.attempt_id, binding.launch_operation_id,
        )
        if (
            value["resolution_id"] != binding.correlation.resolution_id
            or value["parent_id"] != binding.correlation.parent_id
            or value["creation_nonce"] != self._owner_state.creation_nonce
            or value["worktree_id"] != binding.descriptor.worktree_ref
        ):
            raise ValueError("The original launch correlation changed")
        return value

    def inspect(
        self, descriptor: RecoveryDescriptor, operation_id: str, phase: str,
        expected_revision: int, observation: RecoveryObservation | None,
    ) -> dict:
        record = None

        def result(classification: str, reason: str) -> dict:
            return {
                "classification": classification, "reason": reason,
                "effects_wired": False, "native_recovery": "unqualified",
                "reconciliation_required": True, "replay": "never",
                "unknown_effects": list(record.unknown_effects) if record is not None else None,
                "legacy_holds": list(record.legacy_holds) if record is not None else None,
            }

        binding = self._binding
        if (
            type(descriptor) is not RecoveryDescriptor or descriptor != binding.descriptor
            or type(operation_id) is not str or operation_id != self._operation
        ):
            return result("held", "original-binding-mismatch")
        if (
            type(phase) is not str or phase not in {"retire_intent", "resume_intent"}
            or type(expected_revision) is not int or expected_revision < 1
        ):
            return result("held", "invalid-intent-request")
        try:
            owner, _ = launch_recovery_store._binding(self._path)
            if OwnerIntent._from_record(owner) != self._owner_state:
                return result("held", "owner-state-changed")
            if owner.status in _CLOSED:
                return result("held", "owner-intent-closed")
            launch = self._launch()
            record = self._store.load(descriptor)
            if record is None:
                return result("unknown", "intent-unavailable")
            if record.operation_id != operation_id:
                return result("held", "original-operation-mismatch")
            if record.revision != expected_revision or record.steps[-1].phase != phase:
                return result("reconcile", "intent-changed")
            if record.legacy_holds:
                return result("held", "retained-control-hold")
            if type(observation) is not RecoveryObservation:
                return result("unknown", "current-evidence-unavailable")
            current = observation.binding
            if (
                type(current) is not RecoveryBinding or current != binding
                or current.native_owner is not binding.native_owner
                or current._native_scope is not binding._native_scope
            ):
                return result("held", "current-binding-mismatch")
            native_scope = binding._native_scope
            if getattr(binding.native_owner, "native_scope", None) is not native_scope:
                return result("held", "native-scope-changed")
            if observation.source_state not in {"approved", "revoked", "unknown"}:
                return result("unknown", "source-evidence-invalid")
            if observation.source_state != "approved":
                return result(
                    "held" if observation.source_state == "revoked" else "unknown",
                    "current-source-unapproved",
                )
            revision = observation.authority_revision
            if (
                type(revision) is not int or revision < descriptor.lease_revision
                or phase == "resume_intent" and revision != record.steps[-1].authority_revision
            ):
                return result("held", "authority-revision-mismatch")
            if observation.lease is None:
                return result("unknown", "source-lease-unavailable")
            remaining = observation.lease.remaining_seconds()
            if (
                type(remaining) not in (int, float) or not math.isfinite(remaining)
                or not 0 < remaining <= 3600
            ):
                return result("held", "source-lease-unconfirmed")
            if phase == "resume_intent":
                settlement = getattr(binding.native_owner, "scope_settled", None)
                if (
                    observation.scope_settled is not True or native_scope is None
                    or not callable(settlement) or settlement(native_scope) is not True
                ):
                    return result("unknown", "scope-settlement-unconfirmed")
                if getattr(binding.native_owner, "native_scope", None) is not native_scope:
                    return result("reconcile", "native-scope-changed")
            owner, _ = launch_recovery_store._binding(self._path)
            if (
                OwnerIntent._from_record(owner) != self._owner_state or self._launch() != launch
                or self._store.load(descriptor) != record
                or getattr(binding.native_owner, "native_scope", None) is not native_scope
            ):
                return result("reconcile", "owner-or-intent-changed")
        except Exception:
            return result("unknown", "owner-evidence-unavailable")
        return result("candidate", "matching-owned-inputs")
