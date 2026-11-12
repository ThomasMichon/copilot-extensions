"""Effect-free recovery bookkeeping behind the original lifecycle owner's store."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

_PHASES = ("retire_intent", "settled", "resume_intent", "observation_restored")


class RecoveryConflict(ValueError):
    """The original recovery transition must be reconciled, not replayed."""


def _reference(value: object) -> None:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 256
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError("A bounded, nonempty owner reference is required")


def _references(values: tuple[str, ...]) -> None:
    if type(values) is not tuple or len(values) > 64:
        raise ValueError("At most 64 distinct owner references are supported")
    for value in values:
        _reference(value)
    if len(set(values)) != len(values):
        raise ValueError("Repeated owner references are unsupported")


@dataclass(frozen=True)
class RecoveryDescriptor:
    owner_ref: str
    worktree_ref: str
    descriptor_revision: str
    source_owner: str
    source_revision: str
    session_id: str
    environment_ref: str
    boot_ref: str
    scope_ref: str
    incarnation: str
    lease_revision: int

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if key != "lease_revision":
                _reference(value)
        if type(self.lease_revision) is not int or self.lease_revision < 1:
            raise ValueError("An exact positive source lease revision is required")


@dataclass(frozen=True)
class RecoveryStep:
    phase: str
    evidence_ref: str
    unknown_effects: tuple[str, ...] = ()
    authority_revision: int | None = None
    restored_incarnation: str | None = None

    def __post_init__(self) -> None:
        if self.phase not in _PHASES:
            raise ValueError("Unsupported recovery phase")
        _reference(self.evidence_ref)
        _references(self.unknown_effects)
        if self.phase == "resume_intent":
            if type(self.authority_revision) is not int or self.authority_revision < 1:
                raise ValueError("Resume intent requires an explicit authority revision")
        elif self.authority_revision is not None:
            raise ValueError("Only resume intent records renewed authority")
        if self.phase == "observation_restored":
            _reference(self.restored_incarnation)
        elif self.restored_incarnation is not None:
            raise ValueError("Only restored observation records a new incarnation")


@dataclass(frozen=True)
class RecoveryRecord:
    operation_id: str
    descriptor: RecoveryDescriptor
    steps: tuple[RecoveryStep, ...]
    legacy_holds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _reference(self.operation_id)
        if not isinstance(self.descriptor, RecoveryDescriptor):
            raise ValueError("An original-owner recovery descriptor is required")
        if (
            type(self.steps) is not tuple or not 1 <= len(self.steps) <= len(_PHASES)
            or any(not isinstance(step, RecoveryStep) for step in self.steps)
            or tuple(step.phase for step in self.steps) != _PHASES[:len(self.steps)]
        ):
            raise RecoveryConflict("Recovery requires each original transition in order")
        _references(self.legacy_holds)
        for step in self.steps:
            if (
                step.phase == "resume_intent"
                and (
                    step.authority_revision is None
                    or step.authority_revision <= self.descriptor.lease_revision
                )
            ):
                raise RecoveryConflict("Resume authority must be newer than the old lease")
            if (
                step.phase == "observation_restored"
                and step.restored_incarnation == self.descriptor.incarnation
            ):
                raise RecoveryConflict("Restored observation requires a new native incarnation")
        _references(self.unknown_effects)

    @property
    def revision(self) -> int:
        return len(self.steps)

    @property
    def unknown_effects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ref for step in self.steps for ref in step.unknown_effects))

    def status(self) -> dict:
        return {
            "operation_id": self.operation_id, "descriptor": asdict(self.descriptor),
            "revision": self.revision, "phase": self.steps[-1].phase,
            "legacy_holds": list(self.legacy_holds), "unknown_effects": list(self.unknown_effects),
            "reconciliation_required": True, "effects_wired": False,
            "native_recovery": "unqualified", "replay": "never",
        }


class RecoveryStore(Protocol):
    """Owner adapter: durable, atomic CAS; zero denotes an absent operation.

    Returning success must mean the complete record is durably committed.
    Keep one record per exact descriptor and forbid reusing an operation ID
    for another descriptor. A conflicting reservation returns false.
    Exceptions or lost acknowledgements require rereading the same operation.
    Stored evidence references are owner assertions, not authorization tokens.
    """

    def load(self, descriptor: RecoveryDescriptor) -> RecoveryRecord | None: ...

    def compare_and_swap(
        self, descriptor: RecoveryDescriptor, expected_revision: int, record: RecoveryRecord,
    ) -> bool: ...


def reduce_recovery(
    current: RecoveryRecord | None, descriptor: RecoveryDescriptor, operation_id: str,
    step: RecoveryStep, *, legacy_holds: tuple[str, ...] = (),
) -> RecoveryRecord:
    _reference(operation_id)
    _references(legacy_holds)
    if current is None:
        return RecoveryRecord(operation_id, descriptor, (step,), legacy_holds)
    if (
        not isinstance(current, RecoveryRecord)
        or current.operation_id != operation_id or current.descriptor != descriptor
    ):
        raise RecoveryConflict("The original operation and exact descriptor are required")
    holds = tuple(dict.fromkeys((*current.legacy_holds, *legacy_holds)))
    for recorded in current.steps:
        if recorded.phase == step.phase:
            if recorded != step or holds != current.legacy_holds:
                raise RecoveryConflict("A recorded recovery step cannot be rewritten")
            return current
    return RecoveryRecord(operation_id, descriptor, (*current.steps, step), holds)


def record_recovery_step(
    store: RecoveryStore, descriptor: RecoveryDescriptor, operation_id: str,
    expected_revision: int, step: RecoveryStep, *, legacy_holds: tuple[str, ...] = (),
) -> RecoveryRecord:
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("An exact store revision is required")
    _reference(operation_id)
    current = store.load(descriptor)
    proposed = reduce_recovery(
        current, descriptor, operation_id, step, legacy_holds=legacy_holds,
    )
    if current is not None and proposed is current:
        return current
    actual_revision = current.revision if current is not None else 0
    if actual_revision != expected_revision:
        raise RecoveryConflict("The owner store changed; reread the original transition")
    if not store.compare_and_swap(descriptor, expected_revision, proposed):
        raise RecoveryConflict("The owner store changed; reconcile without repeating effects")
    return proposed


def crash_candidate(
    descriptor: RecoveryDescriptor, observation: dict | None,
    *, unknown_effects: tuple[str, ...] = (),
) -> dict:
    """Export suspicion only; never infer retirement authority or an idle session."""
    _references(unknown_effects)
    phase, hold = "unknown", None
    if observation is not None:
        if type(observation) is not dict or any(
            observation.get(key) != getattr(descriptor, key) for key in (
                "source_owner", "source_revision", "session_id", "incarnation", "lease_revision",
            )
        ):
            raise RecoveryConflict("Observation does not match the exact recovery descriptor")
        if type(observation.get("lease_revision")) is not int:
            raise RecoveryConflict("An exact observed lease revision is required")
        phase, hold = observation.get("phase"), observation.get("hold")
        if not isinstance(phase, str) or phase not in {
            "starting", "running", "stopping", "auth_wait", "held", "closed",
        }:
            raise ValueError("Unsupported observed receiver phase")
        if hold is not None:
            _reference(hold)
    return {
        "contract": "copilot-driver.headed-crash-candidate", "version": 1,
        "descriptor": asdict(descriptor), "observed_phase": phase, "legacy_hold": hold,
        "classification": "unknown" if observation is None else (
            "investigate" if hold or phase in {"stopping", "held", "closed"} else "observe"
        ),
        "unknown_effects": list(unknown_effects), "retirement_authorized": False,
        "effects_wired": False, "native_recovery": "unqualified", "replay": "never",
    }
