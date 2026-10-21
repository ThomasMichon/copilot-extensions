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

from .queue_execution_custody import require_execution_send_reconciliation
from .queue_records import SpawnState, Status, TaskError


class SpawnRearmMixin:
    """``rearm_spawn`` for :class:`TaskQueue`."""

    def authorize_fresh_acquisition(
        self, task_id: str, *, reservation_key: str, worktree: str,
        expected_generation: int, permitted: bool = False,
        confirmed_no_live_holder: bool = False, reason: str,
    ) -> dict[str, object]:
        """Authorize a new conversation, never certify historical launch absence."""
        if not permitted or not confirmed_no_live_holder or not reason.strip():
            raise TaskError("fresh acquisition requires permission, verified no-live-holder attestation and reason")
        if type(expected_generation) is not int or expected_generation < 0:
            raise TaskError("fresh acquisition requires an exact non-negative task generation")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id=? ORDER BY attempt DESC",
                (task_id,),
            ).fetchall()
            if (
                task is None or task.status != Status.QUEUED or task.owner is not None
                or task.hold_reason is not None or task.generation != expected_generation
                or task.owner_session_id or not rows or task.exclusive_key is None
            ):
                raise TaskError("fresh acquisition task generation/ownership fence changed")
            latest = rows[0]
            latest_allocation = conn.execute(
                "SELECT key FROM spawn_reservations WHERE exclusive_key=? AND worktree IS NOT NULL "
                "ORDER BY reserved_at DESC LIMIT 1", (task.exclusive_key,),
            ).fetchone()
            if (
                latest["key"] != reservation_key or latest["worktree"] != worktree
                or latest_allocation is None or latest_allocation["key"] != reservation_key
                or latest["state"] not in {SpawnState.DEFERRED, SpawnState.FAILED}
                or latest["session_handle"] or latest["release_requested"]
                or latest["conversation_retired"]
                or latest["worktree_ownership"] not in {"created", "reused"}
                or latest["conclusion_state"] == "pending"
                or any(row["state"] in SpawnState.ACTIVE for row in rows)
            ):
                raise TaskError("fresh acquisition reservation fence changed or remains active")
            foreign = conn.execute(
                "SELECT key FROM spawn_reservations WHERE (worktree=? OR inherited_worktree=?) AND task_id!=? "
                "AND state IN ('reserving','spawned','cold','releasing') LIMIT 1",
                (worktree, worktree, task_id),
            ).fetchone()
            if foreign:
                raise TaskError("another task has an active reservation on the worktree")
            exclusive_owner = conn.execute(
                "SELECT key FROM spawn_reservations WHERE exclusive_key=? "
                "AND state IN ('reserving','spawned','cold','releasing') "
                "AND (exclusive_released=0 OR task_id=?) LIMIT 1",
                (task.exclusive_key, task_id),
            ).fetchone()
            if exclusive_owner:
                raise TaskError("exclusive-key spawn authority is still active")
            require_execution_send_reconciliation(
                conn, task_id, exclusive_key=task.exclusive_key,
            )
            timestamp = self._now(None)
            conn.execute(
                "UPDATE tasks SET generation=generation+1, updated_at=? WHERE id=?",
                (timestamp, task_id),
            )
            conn.execute(
                "UPDATE spawn_reservations SET conversation_retired=1, updated_at=? WHERE key=?",
                (timestamp, reservation_key),
            )
            self._audit(
                conn, task_id, ts=timestamp, from_status=Status.QUEUED,
                to_status=Status.QUEUED, worker="operator",
                note=(
                    f"fresh acquisition authorized: reservation={reservation_key}; "
                    f"worktree={worktree}; generation={expected_generation}->{expected_generation + 1}; "
                    f"no-live-holder attested; history preserved; reason={reason.strip()}"
                ),
            )
            conn.commit()
        return {
            "task_id": task_id, "reservation_key": reservation_key, "worktree": worktree,
            "generation": expected_generation + 1, "conversation_retired": True,
            "authorization": "operator-fresh", "launched": False,
        }

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

            require_execution_send_reconciliation(
                conn, task_id, exclusive_key=task.exclusive_key,
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
