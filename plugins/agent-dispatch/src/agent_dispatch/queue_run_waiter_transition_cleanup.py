from __future__ import annotations

import sqlite3

from .queue_common import RunWaiterWakeOperation, Task
from .queue_records import Status

CLAIM_RELEASE_SENDER = "agent-dispatch-run-waiter-claim-release"


class QueueRunWaiterTransitionCleanupMixin:
    """Run-waiter retirement helpers triggered by non-waiter task transitions."""

    def run_waiter_wake_current(self, wake_id: str, delivery_token: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM run_waiter_wakes WHERE id = ?", (wake_id,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            wake = RunWaiterWakeOperation._from_row(row)
            task = self._fetch(conn, wake.task_id)
            ok = (
                wake.status == "delivering"
                and wake.delivery_token == delivery_token
                and self._run_waiter_wake_is_current(conn, task, wake)
            )
            conn.execute("COMMIT")
        return bool(ok)

    def _retire_waiters_for_transition(
        self,
        conn: sqlite3.Connection,
        task: Task,
        *,
        to_status: str,
        worker: str | None,
        ts: float,
    ) -> bool:
        if task.status != Status.SUSPENDED or to_status == Status.SUSPENDED:
            return False
        row = self._select_waiter_row(conn, task.id, states=("preparing", "active"))
        if row is None:
            return False
        waiter = self._run_waiter_from_row(row)
        conn.execute(
            "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
            " WHERE id = ? AND state IN ('preparing', 'active')",
            (ts, f"task left suspended for {to_status}", row["id"]),
        )
        self._enqueue_run_waiter_wake(conn, waiter, message="", sender=CLAIM_RELEASE_SENDER, ts=ts)
        self._audit(
            conn,
            task.id,
            ts=ts,
            from_status=task.status,
            to_status=to_status,
            worker=worker,
            note=f"run waiter retired (task left suspended for {to_status})",
        )
        return True
