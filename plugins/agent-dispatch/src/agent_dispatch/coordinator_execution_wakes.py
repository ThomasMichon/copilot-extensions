"""Coordinator wake transport uses its real outbox row, not a reservation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .wake_recovery_records import WakeRecoveryRecords

from .bridge_command_delivery import NativePromptNotSent
from .coordinator_execution_sender import CoordinatorExecutionProvenance
from .queue import TaskQueue
from .queue_execution_custody import SendOutcome
from .queue_execution_wakes import Wake, wake_table
from .queue_records import TaskError


def deliver_execution_wake(
    queue: TaskQueue, wake: Wake, incarnation: str | None, deliver: Callable[[], bool],
    *, records: WakeRecoveryRecords | None = None,
) -> bool:
    return _deliver(queue, wake, incarnation, deliver, records=records, no_resume=False)


def deliver_recorded_owner_wake(queue: TaskQueue, wake: Wake, incarnation: str,
                                records: WakeRecoveryRecords) -> bool:
    """Keep admission and receipt lookup on the original no-resume CLI contract."""
    from .owner_wake_delivery import deliver_owner_wake

    if wake_table(wake) != "wake_outbox" or not wake.owner or not wake.owner_session_id:
        raise TaskError("recorded delivery requires an ordinary native owner wake")
    return _deliver(queue, wake, incarnation, lambda: deliver_owner_wake(
        queue, wake.owner, wake.owner_session_id, wake.task_id, wake.message, wake.id, no_resume=True,
    ), records=records, no_resume=True)


def _deliver(
    queue: TaskQueue, wake: Wake, incarnation: str | None, deliver: Callable[[], bool],
    *, records: WakeRecoveryRecords | None, no_resume: bool,
) -> bool:
    from .queue_pool_recovery import prepare_recovery_wake
    prepare_recovery_wake(queue, wake)
    with queue._connect() as conn:
        row = conn.execute(
            f"SELECT execution_send_state FROM {wake_table(wake)} WHERE id = ?", (wake.id,)
        ).fetchone()
    if row is None:
        raise TaskError("wake outbox operation is missing")
    provenance = None
    if row["execution_send_state"] != "sent":
        task = queue.get(wake.task_id)
        if task is None:
            raise TaskError("wake task is missing")
        provenance = CoordinatorExecutionProvenance(queue, task, incarnation or "").prepare()
    if records is None:
        admission = queue.admit_execution_wake(wake, provenance=provenance)
    else:
        if wake_table(wake) != "wake_outbox":
            raise TaskError("independent records require an ordinary wake")
        admission = queue.admit_execution_wake(
            wake, provenance=provenance, no_resume=no_resume, before_commit=lambda conn, task, identity:
            records.retain(conn, task, wake.id, identity, no_resume=no_resume),
        )
    if not admission.send_required:
        return True
    accepted = False
    outcome: SendOutcome = "unknown"
    detail = "coordinator wake transport did not establish acceptance or absence"
    try:
        accepted = deliver()
        if accepted:
            outcome = "sent"
            detail = "coordinator wake transport returned acceptance"
        return accepted
    except NativePromptNotSent as exc:
        if (
            exc.receipt["invocation_key"] != wake.id
            or exc.receipt["native_session_id"] != wake.owner_session_id
        ):
            raise TaskError("native refusal differs from the original wake identity") from exc
        outcome = "not_sent"
        detail = json.dumps(exc.receipt, sort_keys=True, separators=(",", ":"))
        return False
    finally:
        if admission.identity is not None:
            queue.settle_execution_wake(
                wake,
                admission,
                outcome=outcome,
                detail=detail,
            )
            if records is not None and outcome == "sent":
                records.retain_settlement(queue, wake.task_id, wake.id, admission.identity)
