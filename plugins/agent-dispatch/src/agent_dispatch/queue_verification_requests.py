"""Durable submitted-verification trigger queue."""

from __future__ import annotations

import sqlite3
import uuid

from .queue_common import VerificationRequest
from .queue_records import Status, TaskError


class QueueVerificationRequestsMixin:
    """Submitted-verification outbox helpers for :class:`TaskQueue`."""

    def request_submitted_verification(
        self,
        task_id: str,
        *,
        trigger: str,
        not_before: float | None = None,
        now: float | None = None,
    ) -> VerificationRequest:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.SUBMITTED:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot queue submitted verification for a {task.status!r} task"
                )
            if not task.require_verification:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} does not require verification")
            if not task.evaluator_ref:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} has no evaluator_ref to verify against")
            row = self._insert_verification_request(
                conn,
                task_id,
                task.generation,
                trigger,
                ts,
                not_before=not_before,
            )
            conn.execute("COMMIT")
        self._notify_verification()
        return VerificationRequest._from_row(row)

    def schedule_submitted_verification(
        self,
        task_id: str,
        *,
        trigger: str,
        not_before: float,
        now: float | None = None,
    ) -> VerificationRequest:
        ts = self._now(now)
        if not_before <= ts:
            return self.request_submitted_verification(
                task_id,
                trigger=trigger,
                not_before=not_before,
                now=ts,
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.SUBMITTED:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot queue submitted verification for a {task.status!r} task"
                )
            if not task.require_verification:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} does not require verification")
            if not task.evaluator_ref:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} has no evaluator_ref to verify against")
            existing = conn.execute(
                "SELECT * FROM verification_requests WHERE task_id = ? AND generation = ?"
                " AND status = 'pending' ORDER BY not_before ASC, created_at ASC, id ASC LIMIT 1",
                (task_id, task.generation),
            ).fetchone()
            if existing is None:
                row = self._insert_verification_request(
                    conn,
                    task_id,
                    task.generation,
                    trigger,
                    ts,
                    not_before=not_before,
                )
            else:
                current_not_before = float(existing["not_before"])
                if current_not_before > not_before:
                    conn.execute(
                        "UPDATE verification_requests SET not_before = ?, updated_at = ?,"
                        " last_error = NULL WHERE id = ? AND status = 'pending'",
                        (not_before, ts, existing["id"]),
                    )
                row = conn.execute(
                    "SELECT * FROM verification_requests WHERE id = ?",
                    (existing["id"],),
                ).fetchone()
            conn.execute("COMMIT")
        self._notify_verification()
        return VerificationRequest._from_row(row)

    def list_verification_requests(self, task_id: str) -> list[VerificationRequest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM verification_requests WHERE task_id = ? ORDER BY created_at ASC, id ASC",
                (task_id,),
            ).fetchall()
        return [VerificationRequest._from_row(row) for row in rows]

    def has_pending_verification_requests(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM verification_requests WHERE status = 'pending' LIMIT 1"
            ).fetchone()
        return row is not None

    def next_pending_verification_not_before(self) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(not_before) AS next_due FROM verification_requests"
                " WHERE status = 'pending'"
            ).fetchone()
        if row is None or row["next_due"] is None:
            return None
        return float(row["next_due"])

    def recover_inflight_verification_requests(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
    ) -> int:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM verification_requests WHERE status = 'delivering'"
                " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                (max(0.01, lease_seconds), ts),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE verification_requests SET status = 'pending', delivery_token = NULL,"
                    " delivery_expires_at = NULL, not_before = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'delivering'"
                    " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                    (ts, ts, row["id"], max(0.01, lease_seconds), ts),
                )
            conn.execute("COMMIT")
        return len(rows)

    def claim_due_verification_request(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
    ) -> VerificationRequest | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            while True:
                row = conn.execute(
                    "SELECT * FROM verification_requests WHERE status = 'pending' AND not_before <= ?"
                    " ORDER BY created_at ASC, id ASC LIMIT 1",
                    (ts,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                request = VerificationRequest._from_row(row)
                task = self._fetch(conn, request.task_id)
                if not self._verification_request_is_current(task, request):
                    conn.execute(
                        "UPDATE verification_requests SET status = 'stale', updated_at = ?,"
                        " last_error = 'task fence advanced' WHERE id = ? AND status = 'pending'",
                        (ts, request.id),
                    )
                    continue
                token = uuid.uuid4().hex
                cur = conn.execute(
                    "UPDATE verification_requests SET status = 'delivering', attempts = attempts + 1,"
                    " delivery_token = ?, delivery_expires_at = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (token, ts + max(0.01, lease_seconds), ts, request.id),
                )
                if not cur.rowcount:
                    continue
                claimed = conn.execute(
                    "SELECT * FROM verification_requests WHERE id = ?",
                    (request.id,),
                ).fetchone()
                conn.execute("COMMIT")
                return VerificationRequest._from_row(claimed)

    def finish_verification_request(
        self,
        request_id: str,
        delivery_token: str,
        *,
        delivered: bool,
        error: str | None = None,
        max_attempts: int = 8,
        retry_base: float = 1.0,
        now: float | None = None,
    ) -> VerificationRequest:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM verification_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such verification request {request_id!r}")
            request = VerificationRequest._from_row(row)
            if request.status != "delivering" or request.delivery_token != delivery_token:
                conn.execute("COMMIT")
                raise TaskError(
                    f"verification request {request_id!r} is not held by this delivery"
                )
            task = self._fetch(conn, request.task_id)
            if not self._verification_request_is_current(task, request):
                conn.execute(
                    "UPDATE verification_requests SET status = 'stale', updated_at = ?,"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = 'task fence advanced' WHERE id = ?",
                    (ts, request.id),
                )
            elif delivered:
                conn.execute(
                    "UPDATE verification_requests SET status = 'delivered', updated_at = ?,"
                    " delivered_at = ?, delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = NULL WHERE id = ?",
                    (ts, ts, request.id),
                )
            else:
                if request.attempts >= max_attempts:
                    conn.execute(
                        "UPDATE verification_requests SET status = 'failed', updated_at = ?,"
                        " delivery_token = NULL, delivery_expires_at = NULL, last_error = ?"
                        " WHERE id = ?",
                        (ts, error or "verification failed", request.id),
                    )
                else:
                    delay = retry_base * (2 ** max(0, request.attempts - 1))
                    conn.execute(
                        "UPDATE verification_requests SET status = 'pending', updated_at = ?,"
                        " delivery_token = NULL, delivery_expires_at = NULL, not_before = ?,"
                        " last_error = ? WHERE id = ?",
                        (ts, ts + delay, error or "verification failed", request.id),
                    )
            result = conn.execute(
                "SELECT * FROM verification_requests WHERE id = ?",
                (request.id,),
            ).fetchone()
            conn.execute("COMMIT")
        return VerificationRequest._from_row(result)

    @staticmethod
    def _verification_request_is_current(task, request: VerificationRequest) -> bool:
        return bool(
            task is not None
            and task.status == Status.SUBMITTED
            and task.require_verification
            and task.generation == request.generation
        )

    @staticmethod
    def _insert_verification_request(
        conn: sqlite3.Connection,
        task_id: str,
        generation: int,
        trigger: str,
        ts: float,
        *,
        not_before: float | None = None,
    ) -> sqlite3.Row:
        request_id = f"verify:{task_id}:{generation}:{uuid.uuid4().hex[:12]}"
        due_at = ts if not_before is None else float(not_before)
        conn.execute(
            "INSERT INTO verification_requests ("
            " id, task_id, generation, trigger, status, attempts, not_before, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (request_id, task_id, generation, trigger, due_at, ts, ts),
        )
        return conn.execute(
            "SELECT * FROM verification_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
