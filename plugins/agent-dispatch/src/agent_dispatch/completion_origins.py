"""Read-only assignment provenance, never an execution or submission permit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .ordinary_native_bindings import reservation_native_identity
from .queue_pool_placement import pool_worker_matches
from .queue_records import TaskError

# LEFT JOIN is deliberate: an unattributable preparation is unknown, not absent.
# Origin is the immutable acquisition event; continuation can change the native
# and generation without making a different acquisition event the proposal owner.
PREPARATION_ORIGINS = """
WITH proposals AS (
    SELECT id, task_id, worker, CASE WHEN json_valid(submission_preparation) THEN submission_preparation END AS value
    FROM task_events WHERE submission_preparation IS NOT NULL
), assignments AS (
    SELECT id, task_id, worker, CASE WHEN json_valid(task_relationship) THEN task_relationship END AS value
    FROM task_events WHERE task_relationship IS NOT NULL
)
SELECT p.task_id, p.id AS preparation_event_id, e.id AS assignment_event_id
FROM proposals p LEFT JOIN assignments e ON e.task_id=p.task_id AND e.id<p.id
    AND e.id=json_extract(p.value,'$.request.assignment_event_id')
    AND json_type(p.value,'$.version')='integer' AND json_extract(p.value,'$.version')=1
    AND json_type(p.value,'$.request.assignment_event_id')='integer'
    AND json_extract(p.value,'$.request.task_id')=p.task_id AND json_extract(e.value,'$.request.task_id')=e.task_id
    AND json_extract(e.value,'$.request.identity.assignment_event_id')=e.id
    AND json_extract(e.value,'$.request.identity.worker_id')=e.worker
    AND json_extract(e.value,'$.request.event_id')=e.id AND json_type(e.value,'$.request.request_id')='text'
    AND json_extract(p.value,'$.request.assignment_request_id')=json_extract(e.value,'$.request.request_id')
    AND json_type(p.value,'$.request.workspace')='object'
    AND json_extract(p.value,'$.request.workspace')=json_extract(e.value,'$.request.workspace')
    AND json_extract(p.value,'$.request.worker_id')=p.worker
    AND instr(p.worker,'/')>1 AND instr(e.worker,'/')>1
    AND lower(trim(substr(p.worker,1,instr(p.worker,'/')-1)))=lower(trim(substr(e.worker,1,instr(e.worker,'/')-1)))
    AND substr(p.worker,instr(p.worker,'/')+1)=substr(e.worker,instr(e.worker,'/')+1)
    AND json_type(p.value,'$.request.generation')='integer'
    AND ((json_type(p.value,'$.request.assignment_continuation')='null'
          AND json_extract(p.value,'$.request.generation')=json_extract(e.value,'$.request.generation')) OR
         (json_type(p.value,'$.request.assignment_continuation')='object'
          AND json_type(p.value,'$.request.assignment_continuation.generation')='integer'
          AND json_extract(p.value,'$.request.generation')=json_extract(p.value,'$.request.assignment_continuation.generation')
          AND json_extract(p.value,'$.request.generation')>=json_extract(e.value,'$.request.generation')))
    AND json_type(p.value,'$.request.owner_session_id')='text' AND length(json_extract(p.value,'$.request.owner_session_id'))>0
    AND (json_type(p.value,'$.request.assignment_continuation.owner_session_id') IS NULL OR
         json_extract(p.value,'$.request.assignment_continuation.owner_session_id')=json_extract(p.value,'$.request.owner_session_id'))
    AND json_extract(p.value,'$.request.queue_identity')=json_extract(e.value,'$.request.recovery.queue_identity')
    AND json_extract(p.value,'$.request.queue_identity')=(SELECT identity FROM workspace_queue_identity WHERE singleton=1)
"""


@dataclass(frozen=True)
class PreparationOrigin:
    preparation_event_id: int
    assignment_event_id: int | None


def preparation_origins(conn: sqlite3.Connection, task_id: str) -> tuple[PreparationOrigin, ...]:
    """Include every retained proposal, including unresolved origins, in audit order."""
    return tuple(
        PreparationOrigin(row["preparation_event_id"], row["assignment_event_id"])
        for row in conn.execute(
            "SELECT * FROM ("
            + PREPARATION_ORIGINS
            + ") WHERE task_id=? ORDER BY preparation_event_id",
            (task_id,),
        )
    )


def _digest(result: str | None, result_ref: str | None) -> str:
    # Frame null, text and the two separate arguments without copying result bodies into receipts.
    return hashlib.sha256(
        json.dumps([result, result_ref], separators=(",", ":")).encode()
    ).hexdigest()


def _terminal_origin(
    conn: sqlite3.Connection,
    task_id: str,
    generation: int,
    native_id: str,
) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT s.*, t.completed_by, t.completed_at, t.result, t.result_ref,
               p.machine AS pool_machine, e.id AS completion_event_id,
               q.identity AS queue_identity
        FROM tasks t JOIN task_pool_assignments p ON p.task_id=t.id AND p.task_generation=?
        JOIN spawn_reservations s ON s.key=p.reservation_key AND s.task_id=t.id
        JOIN pool_reservation_bindings b ON b.reservation_key=s.key AND b.registration_id=p.registration_id
          AND b.machine=p.machine AND b.env=p.env
        JOIN workspace_queue_identity q ON q.singleton=1
        JOIN task_events e INDEXED BY idx_task_events_task ON e.task_id=t.id AND e.worker=t.completed_by AND e.ts=t.completed_at
            AND e.note='complete' AND e.from_status IN ('started','suspended') AND e.to_status='submitted'
        WHERE t.id=? AND t.status='completed' AND t.owner IS NULL
          AND s.driver IN ('cli','ordinary-bridge') AND s.task_relationship_required=0
          AND s.task_relationship_event_id IS NULL AND s.state IN ('failed','settled','rearmed')
          AND s.release_requested=1 AND s.native_release_pending=0 AND s.cooling_token IS NULL
          AND NOT EXISTS (SELECT 1 FROM task_events later INDEXED BY idx_task_events_task WHERE later.task_id=t.id AND later.id>e.id
                          AND (later.note='complete' OR later.note='complete retry: result recorded'
                               OR later.to_status NOT IN ('submitted','completed')))
        LIMIT 2""",
        (generation, task_id),
    ).fetchall()
    if len(rows) != 1:
        return None
    r = rows[0]
    if (
        not isinstance(r["execution_machine"], str)
        or not pool_worker_matches(r["completed_by"], r["execution_machine"], r["worktree"])
        or not pool_worker_matches(r["completed_by"], r["pool_machine"], r["worktree"])
    ):
        return None
    try:
        observed_native = reservation_native_identity(conn, r)
    except TaskError:
        return None
    if observed_native != native_id:
        return None
    return {
        "version": 1,
        "queue_identity": r["queue_identity"],
        "task_id": task_id,
        "completion_event_id": r["completion_event_id"],
        "completed_at": r["completed_at"],
        "generation": generation,
        "worker_id": r["completed_by"],
        "native_id": native_id,
        "reservation_key": r["key"],
        "result_sha256": _digest(r["result"], r["result_ref"]),
    }


def capture_terminal_detachment(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    """Called in the verified detach transaction, before generation/native are changed."""
    task = conn.execute(
        """
        SELECT t.generation, t.owner_session_id FROM tasks t WHERE t.id=? AND t.status='completed'
          AND 1=(SELECT count(*) FROM task_attachments a WHERE a.task_id=t.id
                 AND a.session_id=t.owner_session_id AND a.attached_at<=t.completed_at AND a.detached_at IS NULL)
    """,
        (task_id,),
    ).fetchone()
    if task is None or not task["owner_session_id"]:
        return None
    return _terminal_origin(conn, task_id, task["generation"], task["owner_session_id"])


def exact_detached_completion(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    worker_id: str,
    encoded_result: str | None,
    result_ref: str | None,
) -> bool:
    """Recognize an exact read-only retry; false includes old or ambiguous evidence."""
    if not conn.in_transaction:
        raise ValueError("completion replay evidence requires a queue read transaction")
    rows = conn.execute(
        """
        WITH receipts AS (
            SELECT task_id, request_id, CASE WHEN json_valid(result_json) THEN result_json END AS result,
                   CASE WHEN json_valid(request_json) THEN request_json END AS request
            FROM task_recovery_receipts WHERE task_id=?
        ) SELECT r.result, t.generation, EXISTS (
            SELECT 1 FROM task_attachments a WHERE a.task_id=t.id
              AND a.session_id=json_extract(r.result,'$.completion_origin_v1.native_id')
              AND a.attached_at<=t.completed_at AND a.detached_at>=t.completed_at
              AND a.detached_at=json_extract(r.result,'$.task.updated_at')
              AND a.detach_reason=('detach_agent: ' || json_extract(r.request,'$.reason'))
        ) AS matched_attachment
        FROM receipts r JOIN tasks t ON t.id=r.task_id
        WHERE t.status='completed' AND t.owner IS NULL AND t.owner_session_id IS NULL
          AND t.completed_by=? AND t.result IS ? AND t.result_ref IS ?
          AND json_extract(r.request,'$.operation')='detach_agent'
          AND json_extract(r.result,'$.operation')='detach_agent' AND json_type(r.result,'$.replayed')='false'
          AND json_extract(r.result,'$.request_id')=r.request_id AND json_extract(r.result,'$.task.id')=t.id
          AND json_type(r.result,'$.task.generation')='integer' AND json_extract(r.result,'$.task.generation')=t.generation
          AND json_extract(r.result,'$.task.status')='completed' AND json_type(r.result,'$.task.owner')='null'
          AND json_type(r.result,'$.task.owner_session_id')='null'
        LIMIT 2""",
        (task_id, worker_id, encoded_result, result_ref),
    ).fetchall()
    if len(rows) != 1 or not rows[0]["matched_attachment"]:
        return False
    receipt, generation, _ = rows[0]
    origin = json.loads(receipt).get("completion_origin_v1")
    if (
        not isinstance(origin, dict)
        or type(origin.get("version")) is not int
        or origin["version"] != 1
        or type(origin.get("generation")) is not int
        or origin["generation"] + 1 != generation
        or not isinstance(origin.get("native_id"), str)
        or not origin["native_id"]
    ):
        return False
    actual = _terminal_origin(conn, task_id, generation - 1, origin["native_id"])
    return actual is not None and json.dumps(origin, sort_keys=True) == json.dumps(
        actual, sort_keys=True
    )
