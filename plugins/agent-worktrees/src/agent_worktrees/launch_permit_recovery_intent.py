"""Original-owner durable intent data; no recovery admission or native effects."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from agent_remote_driver.headed.recovery import (
    PERMIT_INTENT_CONTRACT, PERMIT_INTENT_VERSION, PermitRecoveryIntent, RecoveryDescriptor,
)
from agent_remote_driver.headed.recovery_codec import (
    MAX_RECORD_BYTES, decode_permit_recovery_intent, encode_permit_recovery_intent,
)

from . import launch_execution_permit as permits, launch_recovery_store as store
from . import locks, status_monitor_runtime, tracking, tracking_write
from .execution_spaces import require_project_record_mutation

_VERB = "launch_permit_recovery_intent_record"
_SCHEMA = "worktree-launch-permit-recovery-intents"


def writer_contract() -> dict:
    return {
        "contract": PERMIT_INTENT_CONTRACT, "versions": [PERMIT_INTENT_VERSION],
        "scope": "durable-intent-only", "verb": _VERB, "verb_version": 1,
        "effects_wired": False, "native_recovery": "unqualified", "replay": "never",
    }


def _target(path: Path) -> Path:
    return path.parent / path.stem / "launch-permit-recovery-intents.json"


def _bound(binding: permits.ExecutionBinding, intent: PermitRecoveryIntent) -> None:
    descriptor, owner = intent.descriptor, binding.owner_intent
    store._descriptor(descriptor, tracking.format_claim_ref(
        owner.execution_machine, owner.repo, owner.worktree_id,
    ), owner.worktree_id)
    if (
        intent.permit_id != binding.permit_id
        or (
            descriptor.session_id, descriptor.environment_ref, descriptor.boot_ref,
            descriptor.scope_ref, descriptor.incarnation,
        ) != (
            binding.conversation_ref, binding.environment_ref, binding.boot_ref,
            binding.scope_ref, binding.incarnation_ref,
        )
    ):
        raise ValueError("Recovery intent does not match the retained execution binding")


def _append(entries: list, binding: permits.ExecutionBinding, intent: PermitRecoveryIntent) -> None:
    _bound(binding, intent)
    previous = []
    for old_binding, old in entries:
        if old.descriptor == intent.descriptor or old.operation_id == intent.operation_id:
            if (
                old.descriptor != intent.descriptor or old.operation_id != intent.operation_id
                or old_binding != binding
                or old.original_permit_revision != intent.original_permit_revision
            ):
                raise ValueError("Original recovery intent binding changed")
            previous.append(old)
    if (
        len(previous) >= 2
        or intent.phase != ("retire_intent" if not previous else "resume_intent")
        or previous and intent.permit_revision <= previous[-1].permit_revision
    ):
        raise ValueError("Recovery intent must append the next original phase")
    if len(entries) >= store._MAX_RECORDS:
        raise ValueError("Recovery intent store is full; retained intents cannot be discarded")
    entries.append((binding, intent))


def _read(path: Path, nonce: str, owner: str, worktree_id: str) -> list:
    value = store._read_document(path)
    if value is store._MISSING:
        return []
    if (
        type(value) is not dict
        or set(value) != {"schema", "version", "creation_nonce", "intents"}
        or value["schema"] != _SCHEMA or type(value["version"]) is not int
        or value["version"] != 1 or value["creation_nonce"] != nonce
        or type(value["intents"]) is not list
        or not 1 <= len(value["intents"]) <= store._MAX_RECORDS
    ):
        raise ValueError("Invalid permit recovery intent store binding or contract")
    entries = []
    for item in value["intents"]:
        if (
            type(item) is not dict or set(item) != {"binding", "intent"}
            or type(item["intent"]) is not str
        ):
            raise ValueError("A complete encoded permit recovery intent is required")
        binding = permits._binding(item["binding"])
        intent = decode_permit_recovery_intent(item["intent"].encode("utf-8"))
        store._descriptor(intent.descriptor, owner, worktree_id)
        if binding.owner_intent.creation_nonce != nonce:
            raise ValueError("Original recovery intent incarnation changed")
        _append(entries, binding, intent)
    return entries


def _find(entries: list, binding: permits.ExecutionBinding, intent: PermitRecoveryIntent):
    for old_binding, old in entries:
        if (
            old.descriptor == intent.descriptor or old.operation_id == intent.operation_id
        ) and old.phase == intent.phase:
            if old_binding != binding or old != intent:
                raise ValueError("Original recovery intent already has different data")
            return old
    return None


def _apply(args: dict) -> dict:
    if (
        type(args) is not dict or set(args) != {"yaml_path", "binding", "intent"}
        or type(args["yaml_path"]) is not str or type(args["intent"]) is not str
        or len(args["intent"]) > MAX_RECORD_BYTES
    ):
        raise ValueError("Invalid permit recovery intent request")
    binding = permits._binding(args["binding"])
    intent = decode_permit_recovery_intent(args["intent"].encode("utf-8"))
    _bound(binding, intent)
    path = Path(args["yaml_path"])
    permits._owner(path, binding)
    store._safe_path(path.with_suffix(".lock"))
    with tracking._RecordLock(path, require_sidecar=True):
        record, owner = permits._owner(path, binding)
        require_project_record_mutation(record)
        entries = _read(_target(path), record.creation_nonce, owner, record.worktree_id)
        if _find(entries, binding, intent) is not None:
            return {"committed": True}
        permits._launch(path, binding)
        recovery = next((
            item for item in store._read(
                path.parent / path.stem / "launch-recovery.json",
                record.creation_nonce, owner, record.worktree_id,
            ) if item.descriptor == intent.descriptor
        ), None)
        if (
            recovery is None or recovery.operation_id != intent.operation_id
            or recovery.revision != intent.record_revision
            or recovery.steps[-1].phase != intent.phase
        ):
            raise ValueError("Original recovery operation or intent revision changed")
        permit = permits._current(permits._read(
            permits._target(path), record.creation_nonce, owner, record.worktree_id,
        ), binding)
        if permit is None or permit.revision != intent.permit_revision:
            raise ValueError("Original execution permit revision changed")
        original = permit.decisions[intent.original_permit_revision - 1]
        decision = permit.decisions[-1]
        if (
            original.action != "start" or original.state != "approved"
            or decision.decision_ref != intent.decision_ref
            or decision.action != intent.action or decision.state != "approved"
        ):
            raise ValueError("An exact retained action-specific execution decision is required")
        _append(entries, binding, intent)
        raw = json.dumps({
            "schema": _SCHEMA, "version": 1, "creation_nonce": record.creation_nonce,
            "intents": [{
                "binding": asdict(item_binding),
                "intent": encode_permit_recovery_intent(item).decode("utf-8"),
            } for item_binding, item in entries],
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        store._publish(_target(path), raw)
    return {"committed": True}


class WorktreePermitRecoveryIntents:
    """Append data for one independently retained launch; never admit effects.

    The original v1 recovery record remains the source of unknown work and
    holds. Saved decisions/references cannot prove approval, current source,
    native identity or settlement. No current-source calls run under the lock.
    """

    def __init__(self, record_path: Path, binding: permits.ExecutionBinding):
        if type(binding) is not permits.ExecutionBinding:
            raise ValueError("An independently retained execution binding is required")
        permits._owner(record_path, binding)
        self._path, self._binding = record_path, binding

    def load(self, descriptor: RecoveryDescriptor) -> tuple[PermitRecoveryIntent, ...]:
        record, owner = permits._owner(self._path, self._binding)
        store._descriptor(descriptor, owner, record.worktree_id)
        entries = _read(_target(self._path), record.creation_nonce, owner, record.worktree_id)
        matching = []
        for binding, intent in entries:
            if intent.descriptor == descriptor:
                if binding != self._binding:
                    raise ValueError("Original recovery intent binding changed")
                matching.append(intent)
        return tuple(matching)

    def record_intent(self, intent: PermitRecoveryIntent) -> PermitRecoveryIntent:
        payload = encode_permit_recovery_intent(intent)
        _bound(self._binding, intent)
        for old in self.load(intent.descriptor):
            if old.phase == intent.phase:
                if old != intent:
                    raise ValueError("Original recovery intent already has different data")
                return old
        result = tracking_write.dispatch(
            _VERB, {
                "yaml_path": str(self._path), "binding": asdict(self._binding),
                "intent": payload.decode("utf-8"),
            },
            read_lock_data=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
            ensure_monitor=None, boot_wait_s=0.0, min_version=1,
        )
        if type(result) is not dict or result != {"committed": True} or type(result["committed"]) is not bool:
            raise ValueError("Recovery intent acknowledgement unconfirmed; reread the original operation")
        return intent


tracking_write.register_verb(_VERB, _apply, version=1)
