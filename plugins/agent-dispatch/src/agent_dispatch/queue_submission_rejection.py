"""Exact-submission rejection with same-session recovery and durable delivery."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from .producers.evaluator import Reject
from .queue_common import CompletionOutcome, _task_transition_spec
from .queue_execution_handoff import require_execution_admission
from .queue_records import SpawnState, Status, TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue


class QueueSubmissionRejectionMixin:
    """Coordinator-only recovery; never reassign an evaluator's rejected work."""

    def reject_submission(
        self,
        task_id: str,
        *,
        reason: str,
        feedback: dict[str, Any] | None = None,
        expected_generation: int,
        expected_owner_session_id: str | None,
        expected_completed_by: str | None,
        expected_updated_at: float,
        now: float | None = None,
    ) -> CompletionOutcome:
        queue = cast("TaskQueue", self)
        decision = Reject(reason, feedback)
        if not expected_completed_by or not expected_owner_session_id:
            raise TaskError("submission rejection requires the exact submitting owner/session")
        ts = queue._now(now)
        allowed, to = _task_transition_spec("reject_submission")
        key = f"reject:{expected_generation}:{expected_updated_at!r}"
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = queue._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            from .review_closeout_rejection import record

            disputed = record(queue, conn, task, decision, key, ts, generation=expected_generation,
                              native=expected_owner_session_id, worker=expected_completed_by, updated_at=expected_updated_at)
            if disputed is not None:
                conn.commit()
                return disputed
            prior = conn.execute(
                "SELECT ts, fields FROM task_steer WHERE task_id = ? AND idempotency_key = ?",
                (task_id, key),
            ).fetchone()
            if prior is not None:
                saved = json.loads(prior["fields"])["verification_rejection"]
                if (
                    saved["reason"] != decision.reason
                    or saved["feedback"] != decision.feedback
                    or task.status != to
                    or task.generation != expected_generation
                    or task.owner_session_id != expected_owner_session_id
                    or task.owner != expected_completed_by
                    or task.updated_at != prior["ts"]
                ):
                    raise TaskError("rejected submission or recovery incarnation changed")
                conn.execute("COMMIT")
                return CompletionOutcome(task, None)
            require_execution_admission(conn, task_id)
            if (
                task.status not in allowed
                or not task.require_verification
                or task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
                or task.completed_by != expected_completed_by
                or task.owner is not None
                or task.updated_at != expected_updated_at
            ):
                raise TaskError("submission changed while rejection was in flight")
            if task.hold_reason is not None:
                raise TaskError(f"task {task_id!r} is held; cannot recover its submission")
            retiring = conn.execute(
                "SELECT state, release_requested, conclusion_state FROM spawn_reservations"
                " WHERE task_id = ? ORDER BY attempt DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if retiring is not None and (
                retiring["release_requested"] or retiring["conclusion_state"] is not None
            ):
                raise TaskError("submitting session retirement already began; cannot recover it safely")
            if retiring is not None and retiring["state"] != SpawnState.SPAWNED:
                raise TaskError("submitting reservation is not spawned; same-session recovery unavailable")
            collision = conn.execute(
                "SELECT id FROM tasks WHERE id <> ? AND status IN (?, ?, ?)"
                " AND (owner = ? OR owner_session_id = ?) LIMIT 1",
                (
                    task_id, Status.CLAIMED, Status.STARTED, Status.SUSPENDED,
                    expected_completed_by, expected_owner_session_id,
                ),
            ).fetchone()
            if collision is not None:
                raise TaskError("submitting owner/session already holds other work")
            fields = {
                "verification_rejection": {
                    "reason": decision.reason,
                    "feedback": decision.feedback,
                    "submission": {
                        "generation": task.generation,
                        "owner_session_id": task.owner_session_id,
                        "updated_at": task.updated_at,
                        "result": task.result,
                        "result_ref": task.result_ref,
                        "completed_by": task.completed_by,
                        "completed_at": task.completed_at,
                    },
                }
            }
            conn.execute(
                "INSERT INTO task_steer (task_id, ts, fields, sender, idempotency_key)"
                " VALUES (?, ?, ?, 'evaluator', ?)",
                (task_id, ts, json.dumps(fields, separators=(",", ":")), key),
            )
            conn.execute(
                "UPDATE tasks SET status = ?, owner = ?, updated_at = ?,"
                " lease_expires_at = ?, last_seen_at = ?, last_liveness = NULL,"
                " activity = NULL, activity_updated_at = ?, completed_at = NULL,"
                " result = NULL, result_ref = NULL, completed_by = NULL,"
                " awaiting_steer = 0, card_draft = NULL, resume_requested = 0,"
                " monitor_kind = NULL, monitor_not_before = NULL WHERE id = ?",
                (to, expected_completed_by, ts, ts + queue.lease_seconds, ts, ts, task_id),
            )
            queue._audit(
                conn, task_id, ts=ts, from_status=task.status, to_status=to,
                worker="evaluator", note=f"submission rejected: {decision.reason}",
            )
            recovered = queue._fetch(conn, task_id)
            assert recovered is not None
            queue._enqueue_wake(
                conn, recovered, ts=ts,
                message=(
                    f"Task {task_id}'s completion claim was rejected: {decision.reason}\n"
                    "Read the task and take its verification_rejection steer for the "
                    "inspected submission and structured feedback. Continue the same goal "
                    "in this session; suspend for intermediate waits, and submit only "
                    "when the whole goal is satisfied."
                ),
            )
            recovered = queue._fetch(conn, task_id)
            assert recovered is not None
            conn.execute("COMMIT")
        queue._notify_wake()
        queue._notify_owned_transition()
        return CompletionOutcome(recovered, "task.rejected")
