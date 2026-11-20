"""Scope a settled decline exception to a newly recorded, ungoverned pool generation."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from .queue_allocation_revisions import RETIRED_BEFORE_ALLOCATION
from .queue_records import TaskError


def _undischarged(task: Literal[":id", "t.id", "NEW.id"]) -> str:
    # The latest assignment must itself have a confirmed decline. Only older
    # direct claims may use an independently closed, audited unassignment.
    return f"""(WITH events AS (
        SELECT id, task_id, worker, task_relationship AS value FROM task_events
        WHERE task_id={task} AND task_relationship IS NOT NULL
    ), declined AS (
        SELECT e.id FROM events e JOIN spawn_reservations r
            ON r.task_id=e.task_id AND r.task_relationship_event_id=e.id
        JOIN pool_worker_decline_closeouts d ON d.reservation_key=r.key
        WHERE json_extract(d.receipt,'$.task_relationship_event_id')=e.id
          AND json_extract(d.receipt,'$.task_id')=e.task_id
          AND json_extract(d.receipt,'$.binding.reservation_key')=r.key
          AND json_extract(d.receipt,'$.binding.generation')=r.task_relationship_generation
          AND json_extract(e.value,'$.state')='closed' AND r.release_requested=1
          AND r.native_release_pending=0 AND r.state='settled'
    ), yielded AS (
        SELECT e.id FROM events e WHERE json_type(e.value,'$.request.reservation_key')='null'
          AND json_extract(e.value,'$.state')='closed' AND json_extract(e.value,'$.receipt.phase')='closed'
          AND json_extract(e.value,'$.request.event_id')=e.id AND json_extract(e.value,'$.request.task_id')=e.task_id
          AND json_extract(e.value,'$.receipt.request_id')=json_extract(e.value,'$.request.request_id')
          AND CASE WHEN json_type(e.value,'$.receipt.producer.identity') IS NULL THEN
              json_extract(e.value,'$.receipt.producer.recovery')=json_extract(e.value,'$.request.recovery')
              AND json_type(e.value,'$.request.recovery.version')='integer'
              AND json_extract(e.value,'$.request.recovery.version')=2
              AND json_extract(e.value,'$.request.recovery.assignment_event_id')=e.id
              AND json_extract(e.value,'$.request.recovery.worker_id')=e.worker
          ELSE json_extract(e.value,'$.receipt.producer.identity.assignment_event_id')=e.id
              AND json_extract(e.value,'$.receipt.producer.identity.worker_id')=e.worker END
          AND json_type(e.value,'$.receipt.producer.closeout.relationship_verification.retired')='true'
          AND json_type(e.value,'$.receipt.producer.closeout.relationship_verification.quiescent')='true'
          AND json_extract(e.value,'$.receipt.producer.closeout.relationship_verification.subject')
              =json_extract(e.value,'$.receipt.ref')
          AND EXISTS (
              SELECT 1 FROM task_events u WHERE u.task_id=e.task_id AND u.id>e.id
                AND u.from_status IN ('claimed','started','suspended') AND u.to_status='queued'
                AND u.unassigned_worker=e.worker AND NOT EXISTS (
                    SELECT 1 FROM task_events n WHERE n.task_id=e.task_id AND n.id>e.id AND n.id<u.id
                      AND (n.task_relationship IS NOT NULL OR (n.to_status='claimed' AND n.from_status IS NOT n.to_status))
                )
          )
    ) SELECT NOT EXISTS (SELECT 1 FROM declined WHERE id=(SELECT MAX(id) FROM events))
          OR EXISTS (SELECT 1 FROM events WHERE id NOT IN (SELECT id FROM declined UNION SELECT id FROM yielded))
          OR EXISTS (
              SELECT 1 FROM spawn_reservations s WHERE s.task_id={task} AND s.task_relationship_required=1
                AND NOT {RETIRED_BEFORE_ALLOCATION} AND NOT (
                    s.task_relationship_event_id IS NOT NULL AND s.task_relationship_event_id IN (SELECT id FROM declined)
                    AND s.release_requested=1 AND s.native_release_pending=0 AND s.state IN ('settled','failed','rearmed')
                )
          ))"""


def admit_ungoverned_replacement(
    conn: sqlite3.Connection, task_id: str, key: str, generation: int
) -> None:
    if conn.execute("SELECT " + _undischarged(":id"), {"id": task_id}).fetchone()[0]:
        raise TaskError("ordinary capture cannot drop a governed assignment")
    # Validate canonical refs with the same AssignmentSubject decoder that admitted
    # the original receipt. SQLite keeps enforcing the audited binding after rollback.
    from .queue_task_relationships import _matches_receipt

    for row in conn.execute(
        "SELECT task_relationship FROM task_events WHERE task_id=? AND task_relationship IS NOT NULL"
        " AND json_type(task_relationship,'$.request.reservation_key')='null'", (task_id,),
    ):
        event = json.loads(row[0])
        if not _matches_receipt(event["receipt"], event["request"], phase="closed"):
            raise TaskError("ordinary capture cannot confirm the original direct assignment receipt")
    conn.execute(
        "UPDATE spawn_reservations SET task_relationship_generation=? WHERE key=?",
        (generation, key),
    )


def relationship_required_condition(task: Literal["t", "NEW"]) -> str:
    return (
        f"((EXISTS (SELECT 1 FROM task_events e WHERE e.task_id={task}.id AND e.task_relationship IS NOT NULL)"
        f" OR EXISTS (SELECT 1 FROM spawn_reservations s WHERE s.task_id={task}.id AND s.task_relationship_required=1))"
        f" AND (NOT EXISTS (SELECT 1 FROM spawn_reservations n WHERE n.task_id={task}.id"
        f" AND n.task_relationship_required=0 AND n.task_relationship_generation={task}.generation)"
        f" OR ({_undischarged('NEW.id' if task == 'NEW' else 't.id')})))"
    )
