"""Durable `run --detach` waiter registrations and event-note journaling."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .queue_common import PROGRESS_SUMMARY_MAX, RunWaiterWakeOperation, Task, _clip
from .queue_records import Status, TaskError
from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER
from .queue_delegated_waiters import DelegatedWaiterBindingMixin
from .queue_run_waiter_records import run_waiter_from_row, select_waiter_row


class QueueRunWaitersMixin(DelegatedWaiterBindingMixin):
    """Detached-run waiter and event-note helpers for :class:`TaskQueue`."""

    _run_waiter_from_row = staticmethod(run_waiter_from_row)
    _select_waiter_row = staticmethod(select_waiter_row)

    def prepare_run_waiter(
        self,
        task_id: str,
        *,
        worker_id: str,
        resume_worktree: str,
        command: list[str],
        host: str | None = None,
        reason: str,
        kind: str = "process",
        delegation: dict | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = self._now(now)
        release_enqueued = False
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX) or "hibernating"
        if not resume_worktree:
            raise TaskError("run waiter preparation requires a resume_worktree")
        delegation = self._validate_waiter_preparation(kind, delegation)
        if delegation is not None:
            command = []
            meaningful = f"PR watch {delegation['repo']}#{delegation['number']}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if delegation is not None:
                existing = self._find_delegated_preparation(conn, task_id, delegation["preparation_id"])
                if existing:
                    expected = {k: v for k, v in existing["delegation"].items() if k != "registration_id"}
                    if (expected != delegation or existing["command"] != command
                            or existing["owner"] != worker_id or existing["host"] != host
                            or existing["resume_worktree"] != resume_worktree):
                        raise TaskError("delegated preparation identity conflict")
                    conn.execute("COMMIT")
                    return existing
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if task.status not in (Status.STARTED, Status.SUSPENDED):
                conn.execute("COMMIT")
                raise TaskError(f"cannot prepare a run waiter on a {task.status!r} task")
            if task.owner_session_id is None:
                conn.execute("COMMIT")
                raise TaskError("run waiter preparation requires an owner_session_id")
            previous = conn.execute("SELECT MAX(generation) AS generation FROM run_waiters WHERE task_id = ?", (task_id,)).fetchone()
            generation = int(previous["generation"] or 0) + 1
            prior_row = self._select_waiter_row(conn, task_id, states=("preparing", "active"))
            conn.execute(
                "UPDATE run_waiters SET state = 'superseded', updated_at = ?,"
                " retired_reason = COALESCE(retired_reason, 'superseded by a new waiter')"
                " WHERE task_id = ? AND state IN ('preparing', 'active')",
                (ts, task_id),
            )
            if prior_row is not None:
                prior = self._run_waiter_from_row(prior_row)
                if self._run_waiter_matches_task(prior, task):
                    self._enqueue_run_waiter_wake(conn, prior, message="", sender=CLAIM_RELEASE_SENDER, ts=ts)
                    release_enqueued = True
            if task.status == Status.STARTED:
                conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ?, activity = NULL,"
                    " activity_updated_at = ?, lease_expires_at = NULL, last_liveness = NULL,"
                    " monitor_kind = NULL, monitor_not_before = NULL"
                    " WHERE id = ?",
                    (Status.SUSPENDED, ts, ts, task_id),
                )
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=Status.STARTED,
                    to_status=Status.SUSPENDED,
                    worker=worker_id,
                    note=f"suspend: {meaningful}",
                )
            conn.execute(
                "INSERT INTO run_waiters ("
                " task_id, generation, task_generation, owner, owner_session_id,"
                " pid, host, start_token, resume_worktree,"
                " command_json, state, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, 0, ?, '', ?, ?, 'preparing', ?, ?)",
                (
                    task_id,
                    generation,
                    task.generation,
                    task.owner or "",
                    task.owner_session_id,
                    host,
                    resume_worktree,
                    json.dumps(command, separators=(",", ":")),
                    ts,
                    ts,
                ),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=Status.SUSPENDED,
                to_status=Status.SUSPENDED,
                worker=worker_id,
                note=f"run waiter prepared (generation {generation})",
            )
            conn.execute(
                "UPDATE run_waiters SET kind = ?, delegation_json = ? WHERE task_id = ? AND generation = ?",
                (kind, json.dumps(delegation) if delegation is not None else None, task_id, generation),
            )
            conn.execute("COMMIT")
        self._notify_run_waiter_prepare()
        if release_enqueued:
            self._notify_wake()
        return {
            "task_id": task_id,
            "generation": generation,
            "task_generation": task.generation,
            "owner": task.owner or "",
            "owner_session_id": task.owner_session_id,
            "resume_worktree": resume_worktree,
            "command": list(command),
            "state": "preparing",
            **({"kind": kind, "delegation": delegation} if kind == "delegated" else {}),
        }

    def arm_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int | None = None,
        host: str | None = None,
        start_token: str | None = None,
        kind: str = "process",
        delegation: dict | None = None,
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
            conn.execute("COMMIT")
        return self._run_waiter_from_row(armed)

    def register_run_waiter(
        self,
        task_id: str,
        *,
        pid: int,
        host: str | None,
        start_token: str | None,
        resume_worktree: str,
        command: list[str],
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = self._now(now)
        if not resume_worktree:
            raise TaskError("run waiter registration requires a resume_worktree")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.SUSPENDED:
                conn.execute("COMMIT")
                raise TaskError(f"cannot register a run waiter on a {task.status!r} task")
            previous = conn.execute(
                "SELECT MAX(generation) AS generation FROM run_waiters WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            generation = int(previous["generation"] or 0) + 1
            conn.execute(
                "UPDATE run_waiters SET state = 'superseded', updated_at = ?,"
                " retired_reason = COALESCE(retired_reason, 'superseded by a new waiter')"
                " WHERE task_id = ? AND state = 'active'",
                (ts, task_id),
            )
            conn.execute(
                "INSERT INTO run_waiters ("
                " task_id, generation, task_generation, owner, owner_session_id,"
                " pid, host, start_token, resume_worktree,"
                " command_json, state, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    task_id,
                    generation,
                    task.generation,
                    task.owner or "",
                    task.owner_session_id,
                    int(pid),
                    host,
                    start_token,
                    resume_worktree,
                    json.dumps(command, separators=(",", ":")),
                    ts,
                    ts,
                ),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=task.owner,
                note=f"run waiter registered (generation {generation})",
            )
            conn.execute("COMMIT")
        return {
            "task_id": task_id,
            "generation": generation,
            "task_generation": task.generation,
            "owner": task.owner or "",
            "owner_session_id": task.owner_session_id,
            "pid": int(pid),
            "host": host,
            "start_token": start_token,
            "resume_worktree": resume_worktree,
            "command": list(command),
            "state": "active",
        }

    def get_active_run_waiter(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_waiters WHERE task_id = ? AND state = 'active' ORDER BY generation DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._run_waiter_from_row(row) if row else None

    def list_active_run_waiters(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_waiters WHERE state = 'active' ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._run_waiter_from_row(row) for row in rows]

    def list_pending_run_waiters(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_waiters WHERE state = 'preparing' ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._run_waiter_from_row(row) for row in rows]

    def list_run_waiter_wakes(self, task_id: str) -> list[RunWaiterWakeOperation]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM run_waiter_wakes WHERE task_id = ? ORDER BY created_at ASC, id ASC", (task_id,)).fetchall()
        return [RunWaiterWakeOperation._from_row(row) for row in rows]

    def supersede_run_waiter(
        self,
        task_id: str,
        *,
        reason: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM run_waiters WHERE task_id = ? AND state = 'active'"
                " ORDER BY generation DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            conn.execute(
                "UPDATE run_waiters SET state = 'superseded', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'active'",
                (ts, reason, row["id"]),
            )
            task = self._fetch(conn, task_id)
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter superseded ({reason})",
                )
            conn.execute("COMMIT")
        waiter["state"] = "superseded"
        waiter["retired_reason"] = reason
        return waiter

    def retire_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int,
        host: str,
        start_token: str,
        reason: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(
                conn,
                task_id,
                states=("active",),
                generation=generation,
                pid=pid,
                host=host,
                start_token=start_token,
            )
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            task = self._fetch(conn, task_id)
            if not self._run_waiter_matches_task(waiter, task):
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'active'",
                (ts, reason, row["id"]),
            )
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter retired ({reason})",
                )
            conn.execute("COMMIT")
        waiter["state"] = "retired"
        waiter["retired_reason"] = reason
        return waiter

    def retire_run_waiter_with_wake(
        self,
        task_id: str,
        *,
        generation: int,
        pid: int | None = None,
        host: str | None = None,
        start_token: str | None = None,
        reason: str,
        message: str,
        sender: str,
        delegation: dict | None = None,
        event: dict | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        wake_enqueued = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(
                conn,
                task_id,
                states=("active", "retired", "superseded", "cancelled") if delegation else ("active",),
                generation=generation,
                pid=pid,
                host=host,
                start_token=start_token,
            )
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            if self._delegated_finish_is_replay(waiter, delegation, event):
                conn.execute("COMMIT")
                return waiter
            task = self._fetch(conn, task_id)
            if not self._run_waiter_matches_task(waiter, task):
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'active'",
                (ts, reason, row["id"]),
            )
            if event is not None:
                conn.execute("UPDATE run_waiters SET event_id = ? WHERE id = ?",
                             (event["event_id"], row["id"]))
            wake = self._enqueue_run_waiter_wake(
                conn,
                waiter,
                message=message,
                sender=sender,
                ts=ts,
            )
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter retired ({reason})",
                )
            conn.execute("COMMIT")
            wake_enqueued = True
        if wake_enqueued:
            self._notify_wake()
        waiter["state"] = "retired"
        waiter["retired_reason"] = reason
        waiter["wake_id"] = wake.id
        return waiter

    def recover_preparing_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        reason: str,
        message: str,
        sender: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        wake_enqueued = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(
                conn,
                task_id,
                states=("preparing",),
                generation=generation,
            )
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            task = self._fetch(conn, task_id)
            if not self._run_waiter_matches_task(waiter, task):
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'preparing'",
                (ts, reason, row["id"]),
            )
            wake = self._enqueue_run_waiter_wake(
                conn,
                waiter,
                message=message,
                sender=sender,
                ts=ts,
            )
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter recovered ({reason})",
                )
            conn.execute("COMMIT")
            wake_enqueued = True
        if wake_enqueued:
            self._notify_wake()
        waiter["state"] = "retired"
        waiter["retired_reason"] = reason
        waiter["wake_id"] = wake.id
        return waiter

    def supersede_run_waiter_with_wake(
        self,
        task_id: str,
        *,
        reason: str,
        message: str,
        sender: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        wake_enqueued = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(
                conn,
                task_id,
                states=("preparing", "active"),
            )
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            task = self._fetch(conn, task_id)
            if not self._run_waiter_matches_task(waiter, task):
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE run_waiters SET state = 'superseded', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state IN ('preparing', 'active')",
                (ts, reason, row["id"]),
            )
            wake = self._enqueue_run_waiter_wake(
                conn,
                waiter,
                message=message,
                sender=sender,
                ts=ts,
            )
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter superseded ({reason})",
                )
            conn.execute("COMMIT")
            wake_enqueued = True
        if wake_enqueued:
            self._notify_wake()
        waiter["state"] = "superseded"
        waiter["retired_reason"] = reason
        waiter["wake_id"] = wake.id
        return waiter

    def recover_dead_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        reason: str,
        message: str,
        sender: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        wake_enqueued = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(
                conn, task_id, states=("active",), generation=generation
            )
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            task = self._fetch(conn, task_id)
            if not self._run_waiter_matches_task(waiter, task):
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'active'",
                (ts, reason, row["id"]),
            )
            wake = self._enqueue_run_waiter_wake(
                conn,
                waiter,
                message=message,
                sender=sender,
                ts=ts,
            )
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter recovered ({reason})",
                )
            conn.execute("COMMIT")
            wake_enqueued = True
        if wake_enqueued:
            self._notify_wake()
        waiter["state"] = "retired"
        waiter["retired_reason"] = reason
        waiter["wake_id"] = wake.id
        return waiter

    def abort_preparing_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        reason: str,
        message: str,
        sender: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        return self.recover_preparing_run_waiter(task_id, generation=generation, reason=reason, message=message, sender=sender, now=now)

    def cancel_preparing_run_waiter(
        self,
        task_id: str,
        *,
        generation: int,
        reason: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_waiter_row(conn, task_id, states=("preparing",), generation=generation)
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'preparing'",
                (ts, reason, row["id"]),
            )
            task = self._fetch(conn, task_id)
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter cancelled ({reason})",
                )
            result = conn.execute("SELECT * FROM run_waiters WHERE id = ?", (row["id"],)).fetchone()
            conn.execute("COMMIT")
        return self._run_waiter_from_row(result)

    def has_pending_run_waiter_wakes(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM run_waiter_wakes WHERE status = 'pending' LIMIT 1").fetchone()
        return row is not None

    def recover_inflight_run_waiter_wakes(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
    ) -> int:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM run_waiter_wakes WHERE status = 'delivering'"
                " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                (max(0.01, lease_seconds), ts),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE run_waiter_wakes SET status = 'pending', delivery_token = NULL,"
                    " delivery_expires_at = NULL, not_before = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'delivering'"
                    " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                    (ts, ts, row["id"], max(0.01, lease_seconds), ts),
                )
            conn.execute("COMMIT")
        return len(rows)

    def claim_due_run_waiter_wake(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
    ) -> RunWaiterWakeOperation | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            while True:
                row = conn.execute(
                    "SELECT * FROM run_waiter_wakes WHERE status = 'pending' AND not_before <= ?"
                    " AND (sender = ? OR NOT EXISTS (SELECT 1 FROM execution_admission_closures c"
                    " JOIN tasks t ON t.repo = c.repo AND t.source = c.source"
                    " WHERE t.id = run_waiter_wakes.task_id))"
                    " ORDER BY created_at ASC, id ASC LIMIT 1",
                    (ts, CLAIM_RELEASE_SENDER),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                wake = RunWaiterWakeOperation._from_row(row)
                task = self._fetch(conn, wake.task_id)
                if not self._run_waiter_wake_is_current(conn, task, wake):
                    conn.execute(
                        "UPDATE run_waiter_wakes SET status = 'stale', updated_at = ?,"
                        " last_error = 'task fence advanced' WHERE id = ? AND status = 'pending'",
                        (ts, wake.id),
                    )
                    continue
                token = uuid.uuid4().hex
                cur = conn.execute(
                    "UPDATE run_waiter_wakes SET status = 'delivering', attempts = attempts + 1,"
                    " delivery_token = ?, delivery_expires_at = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (token, ts + max(0.01, lease_seconds), ts, wake.id),
                )
                if not cur.rowcount:
                    continue
                claimed = conn.execute("SELECT * FROM run_waiter_wakes WHERE id = ?", (wake.id,)).fetchone()
                conn.execute("COMMIT")
                return RunWaiterWakeOperation._from_row(claimed)

    def finish_run_waiter_wake(
        self,
        wake_id: str,
        delivery_token: str,
        *,
        delivered: bool,
        error: str | None = None,
        max_attempts: int = 8,
        retry_base: float = 1.0,
        now: float | None = None,
    ) -> RunWaiterWakeOperation:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM run_waiter_wakes WHERE id = ?",
                (wake_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such run waiter wake {wake_id!r}")
            wake = RunWaiterWakeOperation._from_row(row)
            if wake.status != "delivering" or wake.delivery_token != delivery_token:
                conn.execute("COMMIT")
                raise TaskError(f"run waiter wake {wake_id!r} is not held by this delivery")
            task = self._fetch(conn, wake.task_id)
            if not self._run_waiter_wake_is_current(conn, task, wake):
                conn.execute(
                    "UPDATE run_waiter_wakes SET status = 'stale', updated_at = ?,"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = 'task fence advanced' WHERE id = ?",
                    (ts, wake.id),
                )
            elif delivered:
                conn.execute(
                    "UPDATE run_waiter_wakes SET status = 'delivered', updated_at = ?,"
                    " delivered_at = ?, delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = NULL WHERE id = ?",
                    (ts, ts, wake.id),
                )
            else:
                if wake.attempts >= max_attempts:
                    conn.execute(
                        "UPDATE run_waiter_wakes SET status = 'failed', updated_at = ?,"
                        " delivery_token = NULL, delivery_expires_at = NULL, last_error = ?"
                        " WHERE id = ?",
                        (ts, error or "delivery failed", wake.id),
                    )
                else:
                    delay = retry_base * (2 ** max(0, wake.attempts - 1))
                    conn.execute(
                        "UPDATE run_waiter_wakes SET status = 'pending', updated_at = ?,"
                        " delivery_token = NULL, delivery_expires_at = NULL, not_before = ?,"
                        " last_error = ? WHERE id = ?",
                        (ts, ts + delay, error or "delivery failed", wake.id),
                    )
            result = conn.execute("SELECT * FROM run_waiter_wakes WHERE id = ?", (wake.id,)).fetchone()
            conn.execute("COMMIT")
        return RunWaiterWakeOperation._from_row(result)

    def append_event_note(
        self,
        task_id: str,
        *,
        sender: str,
        note: str,
        enqueue_verification: bool = False,
        wake_agent: bool = False,
        now: float | None = None,
    ) -> tuple[Task, int, str | None]:
        ts = self._now(now)
        meaningful = _clip(note, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("event note requires a non-empty note")
        wake_message = (
            f"Task {task_id} received an event note: {meaningful}. "
            "Re-read the task history and handle the new external state before finalizing."
        )
        wake_kind: str | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status in Status.TERMINAL:
                conn.execute("COMMIT")
                raise TaskError(f"cannot append an event note to a {task.status!r} task")
            conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (ts, task_id),
            )
            cur = conn.execute(
                "INSERT INTO task_events (task_id, ts, from_status, to_status, worker, note)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, ts, task.status, task.status, sender, f"event note: {meaningful}"),
            )
            if (
                enqueue_verification
                and task.status == Status.SUBMITTED
                and task.require_verification
                and task.evaluator_ref
            ):
                self._insert_verification_request(conn, task_id, task.generation, "event-note", ts)
            current = self._fetch(conn, task_id)
            if (
                wake_agent
                and current is not None
                and current.status == Status.STARTED
                and current.owner
                and current.owner_session_id is not None
            ):
                self._enqueue_wake(
                    conn,
                    current,
                    message=wake_message,
                    ts=ts,
                )
                wake_kind = "owner"
            elif wake_agent and current is not None and current.status == Status.SUSPENDED:
                waiter_row = self._select_waiter_row(conn, task_id, states=("preparing", "active"))
                if waiter_row is not None:
                    waiter = self._run_waiter_from_row(waiter_row)
                    if self._run_waiter_matches_task(waiter, current):
                        cur_waiter = conn.execute(
                            "UPDATE run_waiters SET state = 'superseded', updated_at = ?,"
                            " retired_reason = ? WHERE id = ? AND state IN ('preparing', 'active')",
                            (ts, "event note wake", waiter_row["id"]),
                        )
                        if cur_waiter.rowcount:
                            self._enqueue_run_waiter_wake(
                                conn,
                                waiter,
                                message=wake_message,
                                sender="agent-dispatch-event-note",
                                ts=ts,
                            )
                            self._audit(
                                conn,
                                task_id,
                                ts=ts,
                                from_status=current.status,
                                to_status=current.status,
                                worker=current.owner,
                                note="run waiter superseded (event note wake)",
                            )
                            wake_kind = "waiter"
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        if (
            enqueue_verification
            and task.status == Status.SUBMITTED
            and task.require_verification
            and task.evaluator_ref
        ):
            self._notify_verification()
        if wake_kind is not None:
            self._notify_wake()
        return result, int(cur.lastrowid), wake_kind  # type: ignore[return-value]

    @staticmethod
    def _run_waiter_matches_task(waiter: dict[str, Any], task: Task | None) -> bool:
        return bool(
            task is not None
            and task.status == Status.SUSPENDED
            and task.owner == waiter["owner"]
            and task.generation == int(waiter["task_generation"])
            and task.owner_session_id == waiter.get("owner_session_id")
        )

    def _run_waiter_wake_is_current(
        self,
        conn: sqlite3.Connection,
        task: Task | None,
        wake: RunWaiterWakeOperation,
    ) -> bool:
        if wake.sender == CLAIM_RELEASE_SENDER:
            return task is not None and task.generation == wake.task_generation
        if not (
            task is not None
            and task.status == Status.SUSPENDED
            and task.owner == wake.owner
            and task.generation == wake.task_generation
            and task.owner_session_id == wake.owner_session_id
        ):
            return False
        row = conn.execute("SELECT MAX(generation) AS generation FROM run_waiters WHERE task_id = ?", (wake.task_id,)).fetchone()
        latest_generation = int((row["generation"] if row is not None else 0) or 0)
        return latest_generation == int(wake.waiter_generation)

    def _enqueue_run_waiter_wake(
        self,
        conn: sqlite3.Connection,
        waiter: dict[str, Any],
        *,
        message: str,
        sender: str,
        ts: float,
    ) -> RunWaiterWakeOperation:
        wake_id = f"run-wake:{waiter['task_id']}:{waiter['generation']}:{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO run_waiter_wakes ("
            " id, task_id, waiter_generation, task_generation, owner, owner_session_id,"
            " waiter_host, resume_worktree, sender, message, status, attempts, not_before,"
            " created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (
                wake_id,
                waiter["task_id"],
                waiter["generation"],
                waiter["task_generation"],
                waiter["owner"],
                waiter.get("owner_session_id"),
                waiter.get("host"),
                waiter["resume_worktree"],
                sender,
                message,
                ts,
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM run_waiter_wakes WHERE id = ?", (wake_id,)).fetchone()
        return RunWaiterWakeOperation._from_row(row)
