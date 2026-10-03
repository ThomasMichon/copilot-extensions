"""``TaskQueue`` mixin: ``rearm_spawn`` -- the operator-administered recovery
for a spawn-dead-lettered task.

Split out of :mod:`agent_dispatch.queue_spawn_reservations` to keep that
module under its module-size cap (same pattern as
:mod:`agent_dispatch.queue_excludes`) rather than grow an already-baselined
file.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned storage helpers such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, and ``self._now()``
and is not usable standalone.
"""

from __future__ import annotations

from .queue_records import SpawnState, Status, TaskError


class SpawnRearmMixin:
    """``rearm_spawn`` for :class:`TaskQueue`."""

    def rearm_spawn(
        self,
        task_id: str,
        *,
        permitted: bool = False,
        reason: str | None = None,
        min_failures: int = 3,
        now: float | None = None,
    ) -> dict[str, object]:
        """Atomically retire failed spawn attempts so one fresh retry is eligible.

        The task must still be queued and unowned, no active reservation may
        exist, and at least ``min_failures`` failed attempts must be present.
        All checks and the failed->rearmed transition share one
        ``BEGIN IMMEDIATE`` transaction with task claims and spawn reservations.

        Also marks each row's ``session_handle`` ``release_requested`` so
        :meth:`SpawnReservationMixin.reserve_spawn`'s carried-session lookup
        stops resurrecting a long-gone session on every later attempt
        (copilot-extensions#4978).
        """
        if not permitted:
            raise TaskError("rearming spawn reservations requires explicit permission")
        reason = (reason or "").strip()
        if not reason:
            raise TaskError("rearming spawn reservations requires a non-empty reason")
        if min_failures < 3:
            raise TaskError("min_failures must be at least 3")

        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.QUEUED or task.owner is not None:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is {task.status!r} with owner "
                    f"{task.owner!r}; rearm requires queued and unowned"
                )
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt ASC",
                (task_id,),
            ).fetchall()
            active = [row["key"] for row in rows if row["state"] in SpawnState.ACTIVE]
            if active:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has active spawn reservation(s): {', '.join(active)}"
                )
            failed = [row for row in rows if row["state"] == SpawnState.FAILED]
            pending_cleanup = [
                row["key"] for row in failed if row["conclusion_state"] == "pending"
            ]
            if pending_cleanup:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has pending spawn cleanup: {', '.join(pending_cleanup)}"
                )
            if len(failed) < min_failures:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has {len(failed)} failed spawn reservation(s); "
                    f"at least {min_failures} required"
                )

            keys: list[str] = []
            for row in failed:
                keys.append(row["key"])
                prior = (row["detail"] or "").strip()
                detail = f"{prior}\nrearmed: {reason}".strip()
                conn.execute(
                    "UPDATE spawn_reservations "
                    "SET state = ?, updated_at = ?, detail = ?, "
                    "release_requested = 1 WHERE key = ?",
                    (SpawnState.REARMED, ts, detail, row["key"]),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=Status.QUEUED,
                to_status=Status.QUEUED,
                worker="operator",
                note=f"spawn reservations rearmed: {reason}",
            )
            conn.execute("COMMIT")
        return {
            "task_id": task_id,
            "rearmed": len(keys),
            "reservation_keys": keys,
            "reason": reason,
            "next_attempt": max(row["attempt"] for row in rows) + 1,
        }
