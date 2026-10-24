"""Explicit restart-confirmed settlement; never replay or manufacture success."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from single_instance_lease.operation import OperationLock, OperationLockedError

from . import boot, foundation_state
from .core import (
    DriverError, Request, StateError, _assert_database_identity, _database,
    _foundation_operation, _request, _root, _upgrade_schema, status,
)


class RecoveryError(DriverError):
    """Positive descendant settlement cannot be proved."""


@dataclass(frozen=True)
class RecoveryRefusal:
    request_id: str
    reason: str


@dataclass(frozen=True)
class RecoveryStatus:
    current_boot_id: str | None
    eligible_ids: tuple[str, ...]
    refused: tuple[RecoveryRefusal, ...]
    error: str | None = None


def _assess(requests: tuple[Request, ...], current: str) -> RecoveryStatus:
    eligible, refused = [], []
    for request in requests:
        if request.state not in {"running", "interrupted", "blocked"} or request.settled_at is not None:
            continue
        reason = None
        if not boot.valid_boot_identity(request.boot_id):
            reason = "missing_or_malformed_admission_boot_identity"
        elif request.boot_id.split(":", 1)[0] != current.split(":", 1)[0]:
            reason = "execution_platform_mismatch"
        elif request.boot_id == current:
            reason = "same_kernel_boot"
        if reason is None:
            eligible.append(request.id)
        else:
            refused.append(RecoveryRefusal(request.id, reason))
    return RecoveryStatus(current, tuple(eligible), tuple(refused))


def recovery_status(*, root: Path | None = None) -> RecoveryStatus:
    """Read-only availability; eligibility is advisory until exclusive reconciliation."""
    try:
        current = boot.kernel_boot_identity()
        if not boot.valid_boot_identity(current):
            raise RecoveryError("native boot identity malformed")
    except Exception as error:
        return RecoveryStatus(None, (), (), f"boot_identity_unavailable:{type(error).__name__}")
    try:
        foundation = _foundation_operation(_root(root))
        if foundation is not None and foundation.fenced:
            return RecoveryStatus(current, (), (), "native_foundation_recovery_required")
        return _assess(status(root=root), current)
    except DriverError as error:
        return RecoveryStatus(current, (), (), f"queue_unavailable:{type(error).__name__}")


def recover_after_restart(*, root: Path | None = None) -> tuple[Request, ...]:
    """Settle old-boot fences atomically under the same installation OperationLock.

    One unprovable fence refuses the entire reconciliation. Pending work remains
    pending; the caller must separately request a normal queue drain/update.
    """
    root = _root(root)
    try:
        foundation = _foundation_operation(root)
    except StateError as error:
        raise RecoveryError(f"foundation recovery state unavailable: {error}") from error
    if foundation is not None and foundation.fenced:
        raise RecoveryError("native foundation requires explicit recovery before queue settlement")
    database_path = root / "queue.sqlite3"
    try:
        identity = database_path.lstat()
    except FileNotFoundError as error:
        raise RecoveryError("queue missing; recovery refused") from error
    # No mkdir or schema creation/repair is allowed as a recovery side effect.
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError as error:
        raise RecoveryError("installation execution is active; recovery refused") from error
    try:
        _assert_database_identity(database_path, identity)
        availability = recovery_status(root=root)
        _assert_database_identity(database_path, identity)
        if availability.error:
            raise RecoveryError(availability.error)
        if availability.refused:
            raise RecoveryError(";".join(
                f"{item.request_id}:{item.reason}" for item in availability.refused
            ))
        if not availability.eligible_ids:
            return ()
        current = availability.current_boot_id
        with _database(root, writable=True, expected_identity=identity) as db:
            db.execute("BEGIN IMMEDIATE")
            # Revalidate all evidence inside the mutation's SQLite transaction.
            requests = tuple(_request(row, root) for row in db.execute("SELECT * FROM requests"))
            assessment = _assess(requests, current)
            if assessment.refused:
                raise RecoveryError("recovery evidence changed; reconciliation refused")
            _upgrade_schema(db, root, lock)
            for identifier in assessment.eligible_ids:
                db.execute(
                    """UPDATE requests SET state='interrupted',settled_boot_id=?,settled_at=?,
                    settlement_kind='kernel_restart',
                    finished_at=COALESCE(finished_at,?),
                    error=COALESCE(error,'previous owner interrupted across kernel restart')
                    WHERE id=?""",
                    (current, time.time(), time.time(), identifier),
                )
            lock.assert_owned()
            _assert_database_identity(database_path, identity)
            settled = tuple(
                _request(db.execute("SELECT * FROM requests WHERE id=?", (identifier,)).fetchone(), root)
                for identifier in assessment.eligible_ids
            )
            db.commit()
            return settled
    except StateError as error:
        raise RecoveryError(f"recovery state unavailable: {error}") from error
    finally:
        lock.release()


def recover_foundation_after_restart(*, root: Path | None = None) -> foundation_state.FoundationOperation | None:
    """Settle only proven old-boot native construction; never repair or replay it."""
    root = _root(root)
    try:
        before = _foundation_operation(root)
    except StateError as error:
        raise RecoveryError(f"foundation recovery state unavailable: {error}") from error
    if before is None:
        return None
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError as error:
        raise RecoveryError("installation execution is active; foundation recovery refused") from error
    try:
        if _foundation_operation(root) != before:
            raise RecoveryError("foundation recovery state changed during observation")
        return foundation_state.recover_after_restart(root, lock)
    except (foundation_state.FoundationStateError, StateError) as error:
        raise RecoveryError(f"foundation recovery refused: {error}") from error
    finally:
        lock.release()
