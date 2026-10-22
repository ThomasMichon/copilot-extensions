"""Coordinator wake transport uses its real outbox row, not a reservation."""

from __future__ import annotations

from collections.abc import Callable

from .coordinator_execution_sender import CoordinatorExecutionProvenance
from .queue import TaskQueue
from .queue_execution_wakes import Wake, wake_table
from .queue_records import TaskError


def deliver_execution_wake(
    queue: TaskQueue, wake: Wake, incarnation: str | None, deliver: Callable[[], bool]
) -> bool:
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
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    if not admission.send_required:
        return True
    accepted = False
    try:
        accepted = deliver()
        return accepted
    finally:
        if admission.identity is not None:
            queue.settle_execution_wake(
                wake,
                admission,
                outcome="sent" if accepted else "unknown",
                detail=(
                    "coordinator wake transport returned acceptance"
                    if accepted else "coordinator wake transport did not establish acceptance or absence"
                ),
            )
