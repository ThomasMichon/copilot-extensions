"""Native waiter arming through the existing queue transaction and admission."""

from __future__ import annotations

from collections.abc import Callable
import json
import sqlite3
from typing import Any, TYPE_CHECKING

from .queue_delegated_waiters import DelegatedWaiterBindingMixin
from .queue_feed_attempts import require_feed_arm


class QueueRunWaiterArmingMixin(DelegatedWaiterBindingMixin):
    """Keep process and delegated arming on their original native owner."""

    if TYPE_CHECKING:
        _select_waiter_row: Callable[..., sqlite3.Row | None]

    def arm_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int | None = None,
        host: str | None = None,
        start_token: str | None = None,
        kind: str = "process",
        delegation: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(conn, task_id, states=("preparing",), generation=generation)
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            self._validate_waiter_arm(waiter, kind=kind, delegation=delegation, pid=pid,
                                      host=host, start_token=start_token)
            task = self._fetch(conn, task_id)
            if not self._run_waiter_matches_task(waiter, task):
                conn.execute("COMMIT")
                return None
            require_feed_arm(conn, task, waiter, delegation, now=self._now(now))
            conn.execute(
                "UPDATE run_waiters SET state = 'active', pid = ?, host = ?, start_token = ?,"
                " updated_at = ? WHERE id = ? AND state = 'preparing'",
                (int(pid or 0), host if kind == "process" else waiter["host"],
                 start_token, ts, row["id"]),
            )
            if delegation is not None:
                conn.execute("UPDATE run_waiters SET delegation_json = ? WHERE id = ?",
                             (json.dumps(delegation), row["id"]))
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter armed (generation {generation})",
                )
            armed = conn.execute("SELECT * FROM run_waiters WHERE id = ?", (row["id"],)).fetchone()
            if waiter["delegation"] is not None and "feed" in waiter["delegation"]:
                require_feed_arm(
                    conn, self._fetch(conn, task_id), waiter, delegation, now=self._now(now),
                )
            conn.execute("COMMIT")
        return self._run_waiter_from_row(armed)
