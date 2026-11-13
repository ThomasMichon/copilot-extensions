"""Original-owner decision bookkeeping, not an execution-authority issuer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
from pathlib import Path
import time

from . import launch_attempt, launch_execution, launch_recovery_store as store
from . import locks, status_monitor_runtime, tracking, tracking_write
from .execution_spaces import require_project_record_mutation
from .launch_recovery_admission import OwnerIntent, SourceSelection, _CLOSED

_VERB = "launch_execution_decision_append"
_SCHEMA = "worktree-launch-execution-decisions"
_MAX_DECISIONS = 64


def _reference(value: object) -> None:
    if (
        type(value) is not str or not 1 <= len(value) <= 256
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError("An exact bounded owner reference is required")


@dataclass(frozen=True)
class ExecutionBinding:
    """Independently retained original-owner inputs; never a recovery request.

    Historical native references are exact bookkeeping keys, not live native
    identity or provider support. The owner retains actual approvals privately.
    """

    permit_id: str
    correlation: launch_execution.Correlation
    launch_operation_id: str
    owner_intent: OwnerIntent
    source: SourceSelection
    conversation_ref: str
    environment_ref: str
    boot_ref: str
    scope_ref: str
    incarnation_ref: str

    def __post_init__(self) -> None:
        launch_attempt.identity(self.permit_id)
        launch_attempt.identity(self.launch_operation_id)
        if (
            type(self.correlation) is not launch_execution.Correlation
            or type(self.owner_intent) is not OwnerIntent
            or type(self.source) is not SourceSelection
        ):
            raise ValueError("Independently retained original-owner inputs are required")
        for name in (
            "conversation_ref", "environment_ref", "boot_ref", "scope_ref", "incarnation_ref",
        ):
            _reference(getattr(self, name))


@dataclass(frozen=True)
class ExecutionDecision:
    """One already-authorized action decision, not a credential or permission.

    decision_ref locates the owner's private evidence and limits. Neither that
    reference nor this value proves those approvals or allows an effect.
    """

    decision_ref: str
    action: str
    state: str
    expires_at: int | float

    def __post_init__(self) -> None:
        _reference(self.decision_ref)
        if (
            type(self.action) is not str or self.action not in {"start", "retire", "restore"}
            or type(self.state) is not str or self.state not in {"approved", "revoked"}
            or type(self.expires_at) not in (int, float)
            or not math.isfinite(self.expires_at) or not 0 < self.expires_at <= 253402300799
        ):
            raise ValueError("An exact action-specific owner decision is required")


@dataclass(frozen=True)
class PermitRecord:
    binding: ExecutionBinding
    decisions: tuple[ExecutionDecision, ...]

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not ExecutionBinding or type(self.decisions) is not tuple
            or not 1 <= len(self.decisions) <= _MAX_DECISIONS
            or any(type(item) is not ExecutionDecision for item in self.decisions)
            or self.decisions[0].action != "start" or self.decisions[0].state != "approved"
            or len({item.decision_ref for item in self.decisions}) != len(self.decisions)
        ):
            raise ValueError("An append-only original execution decision history is required")

    @property
    def revision(self) -> int:
        return len(self.decisions)


def _object(value: object, kind) -> dict:
    if type(value) is not dict or set(value) != {item.name for item in fields(kind)}:
        raise ValueError("Invalid execution decision fields or contract")
    return value


def _binding(value: object) -> ExecutionBinding:
    data = dict(_object(value, ExecutionBinding))
    data["correlation"] = launch_execution.Correlation(
        **_object(data["correlation"], launch_execution.Correlation),
    )
    data["owner_intent"] = OwnerIntent(**_object(data["owner_intent"], OwnerIntent))
    data["source"] = SourceSelection(**_object(data["source"], SourceSelection))
    return ExecutionBinding(**data)


def _target(path: Path) -> Path:
    return path.parent / path.stem / "launch-execution-decisions.json"


def _read(path: Path, nonce: str, owner: str, worktree_id: str) -> list[PermitRecord]:
    value = store._read_document(path)
    if value is store._MISSING:
        return []
    if (
        type(value) is not dict
        or set(value) != {"schema", "version", "creation_nonce", "permits"}
        or value["schema"] != _SCHEMA or type(value["version"]) is not int
        or value["version"] != 1 or value["creation_nonce"] != nonce
        or type(value["permits"]) is not list
        or not 1 <= len(value["permits"]) <= store._MAX_RECORDS
    ):
        raise ValueError("Invalid execution decision binding or contract")
    records = []
    for entry in value["permits"]:
        data = _object(entry, PermitRecord)
        if type(data["decisions"]) is not list:
            raise ValueError("Invalid execution decision history")
        binding = _binding(data["binding"])
        intent = binding.owner_intent
        if (
            intent.creation_nonce != nonce or intent.worktree_id != worktree_id
            or tracking.format_claim_ref(
                intent.execution_machine, intent.repo, intent.worktree_id,
            ) != owner
            or any(
                old.binding.permit_id == binding.permit_id
                or old.binding.launch_operation_id == binding.launch_operation_id
                for old in records
            )
        ):
            raise ValueError("Repeated or foreign original execution permit")
        records.append(PermitRecord(binding, tuple(
            ExecutionDecision(**_object(item, ExecutionDecision)) for item in data["decisions"]
        )))
    return records


def _current(records: list[PermitRecord], binding: ExecutionBinding) -> PermitRecord | None:
    current = next((
        item for item in records
        if item.binding.permit_id == binding.permit_id
        or item.binding.launch_operation_id == binding.launch_operation_id
    ), None)
    if current is not None and current.binding != binding:
        raise ValueError("Original execution permit binding changed")
    return current


def _owner(path: Path, binding: ExecutionBinding):
    record, owner = store._binding(path)
    if (
        record.status in _CLOSED or OwnerIntent._from_record(record) != binding.owner_intent
    ):
        raise ValueError("Original execution owner intent changed or closed")
    return record, owner


def _launch(path: Path, binding: ExecutionBinding) -> None:
    value = launch_execution.read(
        path.parent, binding.correlation.attempt_id, binding.launch_operation_id,
    )
    if (
        value["resolution_id"] != binding.correlation.resolution_id
        or value["parent_id"] != binding.correlation.parent_id
        or value["creation_nonce"] != binding.owner_intent.creation_nonce
        or value["worktree_id"] != binding.owner_intent.worktree_id
    ):
        raise ValueError("Original execution correlation changed")


def _revision(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("An exact execution decision revision is required")


def _apply(args: dict) -> dict:
    if (
        type(args) is not dict
        or set(args) != {"yaml_path", "binding", "expected_revision", "decision"}
        or type(args["yaml_path"]) is not str
    ):
        raise ValueError("Invalid original-owner execution decision append")
    binding = _binding(args["binding"])
    decision = ExecutionDecision(**_object(args["decision"], ExecutionDecision))
    expected = args["expected_revision"]
    _revision(expected)
    path = Path(args["yaml_path"])
    _owner(path, binding)
    store._safe_path(path.with_suffix(".lock"))
    with tracking._RecordLock(path, require_sidecar=True):
        record, owner = _owner(path, binding)
        require_project_record_mutation(record)
        _launch(path, binding)
        target = _target(path)
        records = _read(target, record.creation_nonce, owner, record.worktree_id)
        current = _current(records, binding)
        revision = current.revision if current is not None else 0
        if revision != expected:
            return {"committed": False}
        proposed = PermitRecord(binding, (
            *(current.decisions if current is not None else ()), decision,
        ))
        if current is None:
            if len(records) >= store._MAX_RECORDS:
                raise ValueError("Execution decision store is full; retained permits cannot be discarded")
            records.append(proposed)
        else:
            records[records.index(current)] = proposed
        raw = json.dumps({
            "schema": _SCHEMA, "version": 1, "creation_nonce": record.creation_nonce,
            "permits": [asdict(item) for item in records],
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        store._publish(target, raw)
    return {"committed": True}


class WorktreeExecutionPermit:
    """Actor-free owner journal with no ordinary caller or native wiring.

    The trusted lifecycle owner supplies independently retained decisions.
    This records them; it cannot authenticate their private evidence, obtain
    approval or issue execution authority. No tracking lock spans a callback.
    """

    def __init__(self, record_path: Path, binding: ExecutionBinding):
        if type(binding) is not ExecutionBinding:
            raise ValueError("An independently retained execution binding is required")
        _owner(record_path, binding)
        self._path, self._binding = record_path, binding

    def load(self) -> PermitRecord | None:
        """Read without creating, resetting or upgrading retained state."""
        record, owner = _owner(self._path, self._binding)
        return _current(_read(
            _target(self._path), record.creation_nonce, owner, record.worktree_id,
        ), self._binding)

    def record_decision(self, expected_revision: int, decision: ExecutionDecision) -> PermitRecord:
        """Retain an already-authorized decision; reconcile a lost ACK by reread."""
        _revision(expected_revision)
        if type(decision) is not ExecutionDecision:
            raise ValueError("An independently retained execution decision is required")
        current = self.load()
        if (
            current is not None and current.revision == expected_revision + 1
            and current.decisions[-1] == decision
        ):
            return current
        if (current.revision if current is not None else 0) != expected_revision:
            raise ValueError("Execution decision revision changed; reconcile the original permit")
        proposed = PermitRecord(self._binding, (
            *(current.decisions if current is not None else ()), decision,
        ))
        result = tracking_write.dispatch(
            _VERB, {
                "yaml_path": str(self._path), "binding": asdict(self._binding),
                "expected_revision": expected_revision, "decision": asdict(decision),
            },
            read_lock_data=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
            ensure_monitor=None, boot_wait_s=0.0, min_version=1,
        )
        if (
            type(result) is not dict or set(result) != {"committed"}
            or type(result["committed"]) is not bool
        ):
            raise ValueError("Execution decision acknowledgement unconfirmed; reread the original permit")
        if not result["committed"]:
            raise ValueError("Execution decision revision changed; reconcile the original permit")
        return proposed

    def inspect(
        self, action: str, expected_revision: int, *,
        owned_decision: ExecutionDecision | None, current_source: SourceSelection | None,
        source_state: str,
    ) -> dict:
        """Compare retained data with independent owner inputs, never admit effects.

        A saved decision cannot substitute for owned_decision or its actual
        private approval/limits. Final native admission and a permit-aware
        recovery-intent contract remain unavailable, even for a candidate.
        """
        def result(classification: str, reason: str) -> dict:
            return {
                "classification": classification, "reason": reason,
                "effects_wired": False, "native_recovery": "unqualified",
                "intent_contract": "unavailable", "reconciliation_required": True,
                "replay": "never",
            }

        if type(action) is not str or action not in {"start", "retire", "restore"}:
            return result("held", "unsupported-action")
        if type(expected_revision) is not int or expected_revision < 1:
            return result("held", "invalid-permit-revision")
        try:
            current = self.load()
            if current is None:
                return result("unknown", "decision-unavailable")
            if current.revision != expected_revision:
                return result("reconcile", "permit-revision-changed")
            if type(owned_decision) is not ExecutionDecision:
                return result("unknown", "owned-decision-unavailable")
            if owned_decision != current.decisions[-1]:
                return result("held", "owned-decision-mismatch")
            if owned_decision.state != "approved":
                return result("held", "decision-revoked")
            if owned_decision.action != action or action == "restore" and current.revision <= 1:
                return result("held", "action-not-authorized")
            now = time.time()
            if type(now) not in (int, float) or not math.isfinite(now):
                return result("unknown", "decision-clock-unavailable")
            if now >= owned_decision.expires_at:
                return result("held", "decision-expired")
            if type(current_source) is not SourceSelection:
                return result("unknown", "current-source-unavailable")
            if current_source != self._binding.source:
                return result("held", "current-source-mismatch")
            if source_state != "approved":
                return result(
                    "held" if source_state == "revoked" else "unknown",
                    "current-source-unapproved",
                )
            _launch(self._path, self._binding)
            if self.load() != current:
                return result("reconcile", "permit-revision-changed")
        except Exception:
            return result("unknown", "owner-evidence-unavailable")
        return result("candidate", "matching-owned-decision")


tracking_write.register_verb(_VERB, _apply, version=1)
