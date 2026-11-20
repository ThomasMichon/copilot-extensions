"""Scope a settled decline exception to a newly recorded, ungoverned pool generation."""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from .queue_allocation_revisions import RETIRED_WITHOUT_WORKSPACE
from .queue_records import TaskError


def _undischarged(task: Literal[":id", "t.id", "NEW.id", "OLD.id"], *, before_acquisition: bool = False) -> str:
    from .completion_origins import PREPARATION_ORIGINS

    # A newly bound governed worker can recheck the *earlier* discharge, without
    # treating its own live claim as discharged. No execution guard uses this prefix.
    prior_events = "AND id<:acquisition" if before_acquisition else ""
    prior_reservations = "AND s.task_relationship_event_id IS NOT :acquisition" if before_acquisition else ""
    # The latest assignment must itself have a confirmed decline. Only older
    # direct claims may use an independently closed, audited unassignment.
    return f"""(WITH events AS (
        SELECT id, task_id, worker, task_relationship AS value FROM task_events
        WHERE task_id={task} AND task_relationship IS NOT NULL {prior_events}
    ), declined AS (
        SELECT e.id, r.key AS execution_key FROM events e JOIN spawn_reservations r
            ON r.task_id=e.task_id AND r.task_relationship_event_id=e.id
        JOIN pool_worker_decline_closeouts d ON d.reservation_key=r.key
        WHERE json_extract(d.receipt,'$.task_relationship_event_id')=e.id
          AND json_extract(d.receipt,'$.task_id')=e.task_id
          AND json_extract(d.receipt,'$.binding.reservation_key')=r.key
          AND json_extract(d.receipt,'$.binding.generation')=r.task_relationship_generation
          AND json_extract(e.value,'$.state')='closed' AND r.release_requested=1
          AND r.native_release_pending=0 AND r.state IN ('settled','failed','rearmed')
    ), recovered_roots AS (
        SELECT r.allocation_reservation_key AS key, r.task_id, r.creation_key, r.allocation_revision,
               r.ordinary_admission_policy, r.reserved_by, r.allocation_project, r.allocation_cohort,
               json_extract(d.receipt,'$.observation.allocation.attempt') AS original_attempt
        FROM declined c JOIN spawn_reservations r ON r.key=c.execution_key
        JOIN pool_worker_decline_closeouts d ON d.reservation_key=r.key
        JOIN events e ON e.id=c.id
        JOIN spawn_reservations p ON p.key=json_extract(e.value,'$.request.reservation_key')
        WHERE r.driver='ordinary-bridge' AND p.driver=r.driver AND p.worktree_ownership='created'
          AND p.task_id=r.task_id AND p.task_relationship_event_id=c.id AND p.reserved_by=r.reserved_by
          AND p.allocation_reservation_key=r.allocation_reservation_key AND p.creation_key=r.creation_key
          AND p.allocation_revision=r.allocation_revision AND p.ordinary_admission_policy=r.ordinary_admission_policy
          AND p.worktree_writer_requirement=r.worktree_writer_requirement AND p.creation_nonce=r.creation_nonce
          AND p.worktree=r.worktree AND p.worktree_path=r.worktree_path AND p.allocation_project=r.allocation_project
          AND p.execution_machine=r.execution_machine AND p.allocation_cohort IS r.allocation_cohort
          AND p.cooling_token IS NULL AND r.cooling_token IS NULL AND p.cleanup_claim_token IS NULL AND r.cleanup_claim_token IS NULL
          AND NOT EXISTS (SELECT 1 FROM execution_send_custody x WHERE x.reservation_key IN (p.key,r.key) AND x.state IN ('pending','unknown'))
          AND NOT EXISTS (SELECT 1 FROM wake_outbox w WHERE w.task_id=r.task_id AND w.execution_send_state IN ('pending','unknown'))
          AND NOT EXISTS (SELECT 1 FROM run_waiter_wakes w WHERE w.task_id=r.task_id AND w.execution_send_state IN ('pending','unknown'))
          AND r.session_handle=('local-body:' || json_extract(r.agent_info,'$.session_id'))
          AND json_extract(d.receipt,'$.binding.native_id')=CASE WHEN json_extract(r.agent_info,'$.fact_kind')='resumed'
              THEN json_extract(r.agent_info,'$.acp_session_id') ELSE json_extract(r.agent_info,'$.connection.native_session_id') END
          AND NOT EXISTS (SELECT 1 FROM acquisition_receipts a WHERE a.reservation_key=r.key
                          AND a.native_session_id IS NOT json_extract(d.receipt,'$.binding.native_id'))
          AND json_extract(d.receipt,'$.observation.operation')='observe-ready'
          AND json_type(d.receipt,'$.observation.ready')='true'
          AND json_extract(d.receipt,'$.observation.task_claim_ref')=json_extract(e.value,'$.receipt.ref')
          AND json_extract(d.receipt,'$.observation.task_id')=r.task_id
          AND json_extract(d.receipt,'$.observation.native_id')=json_extract(d.receipt,'$.binding.native_id')
          AND json_extract(d.receipt,'$.observation.project')=r.allocation_project
          AND json_extract(d.receipt,'$.observation.path')=r.worktree_path
          AND json_extract(d.receipt,'$.observation.worktree_id')=r.worktree
          AND json_extract(d.receipt,'$.observation.creation_nonce')=r.creation_nonce
          AND json_extract(d.receipt,'$.observation.reservation_key')=r.allocation_reservation_key
          AND json_extract(d.receipt,'$.observation.allocation.task_id')=r.task_id
          AND json_extract(d.receipt,'$.observation.allocation.reservation_key')=r.allocation_reservation_key
          AND json_extract(d.receipt,'$.observation.allocation.driver')=r.driver
          AND json_extract(d.receipt,'$.observation.allocation.supervisor')=r.reserved_by
          AND json_extract(d.receipt,'$.observation.allocation.ownership')='created'
          AND json_extract(d.receipt,'$.observation.allocation.creator_machine')=r.execution_machine
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
          -- Ignore only an unadvanced, failed proposal made *before* the original
          -- assignment's unassignment. The LEFT JOIN projection preserves unknowns.
          OR EXISTS (
              SELECT 1 FROM ({PREPARATION_ORIGINS}) o JOIN task_events p ON p.id=o.preparation_event_id
              WHERE o.task_id={task} AND (
                  o.assignment_event_id IS NULL OR json_type(p.submission_preparation,'$.advancement') IS NOT NULL
                  OR json_type(p.submission_preparation,'$.blocker.code') IS NOT 'text'
                  OR NOT EXISTS (
                      SELECT 1 FROM events e WHERE e.id=o.assignment_event_id
                        AND e.id IN (SELECT id FROM yielded UNION SELECT id FROM declined)
                        AND p.id < (SELECT MIN(u.id) FROM task_events u WHERE u.task_id=e.task_id
                                    AND u.id>e.id AND u.from_status IN ('claimed','started','suspended')
                                    AND u.to_status='queued' AND u.unassigned_worker=e.worker)
                  )
              )
          )
          OR EXISTS (
              SELECT 1 FROM spawn_reservations s WHERE s.task_id={task} AND s.task_relationship_required=1 {prior_reservations}
                AND NOT ({RETIRED_WITHOUT_WORKSPACE} AND (s.allocation_project IS NULL OR EXISTS (
                    SELECT 1 FROM recovered_roots root WHERE root.key=s.key AND s.key=s.allocation_reservation_key
                      AND s.cooling_token IS NULL AND s.cleanup_claim_token IS NULL
                      AND root.task_id=s.task_id AND root.creation_key=s.creation_key AND root.allocation_revision=s.allocation_revision
                      AND root.original_attempt=s.attempt AND root.allocation_project=s.allocation_project
                      AND root.allocation_cohort IS s.allocation_cohort
                      AND root.reserved_by=s.reserved_by AND root.ordinary_admission_policy=s.ordinary_admission_policy
                ))) AND NOT (
                    s.task_relationship_event_id IS NOT NULL AND s.task_relationship_event_id IN (SELECT id FROM declined)
                    AND s.release_requested=1 AND s.native_release_pending=0 AND s.state IN ('settled','failed','rearmed')
                )
          ))"""


def _confirm_direct_receipts(conn: sqlite3.Connection, task_id: str, *, before: int | None = None) -> None:
    # Validate canonical refs with the decoder that admitted the original receipt.
    from .queue_task_relationships import _matches_receipt
    for row in conn.execute(
        "SELECT task_relationship FROM task_events WHERE task_id=? AND task_relationship IS NOT NULL"
        " AND json_type(task_relationship,'$.request.reservation_key')='null' AND (? IS NULL OR id<?)",
        (task_id, before, before),
    ):
        event = json.loads(row[0])
        if not _matches_receipt(event["receipt"], event["request"], phase="closed"):
            raise TaskError("ordinary capture cannot confirm the original direct assignment receipt")


def prior_proposals_discharged(conn: sqlite3.Connection, task_id: str, acquisition: int) -> bool:
    """Recheck retained proposals before the latest acquisition; never discharge that acquisition."""
    proof = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM task_events c JOIN spawn_reservations n ON n.task_id=c.task_id"
        " WHERE c.task_id=:id AND c.id=:acquisition AND n.task_relationship_required=1"
        " AND n.task_relationship_event_id=c.id AND n.key=json_extract(c.task_relationship,'$.request.reservation_key')"
        " AND n.task_relationship_generation=json_extract(c.task_relationship,'$.request.generation')"
        " AND json_extract(c.task_relationship,'$.state')='bound'"
        " AND NOT EXISTS (SELECT 1 FROM task_events later WHERE later.task_id=c.task_id"
        " AND later.id>c.id AND later.task_relationship IS NOT NULL)"
        " AND n.task_relationship_generation>(SELECT MAX(CASE WHEN json_valid(p.submission_preparation)"
        " THEN json_extract(p.submission_preparation,'$.request.generation') END)"
        " FROM task_events p WHERE p.task_id=c.task_id AND p.submission_preparation IS NOT NULL))"
        " AND NOT " + _undischarged(":id", before_acquisition=True),
        {"id": task_id, "acquisition": acquisition},
    ).fetchone()
    if not proof[0]:
        return False
    _confirm_direct_receipts(conn, task_id, before=acquisition)
    return True


def admit_ungoverned_replacement(
    conn: sqlite3.Connection, task_id: str, key: str, generation: int
) -> None:
    if conn.execute("SELECT " + _undischarged(":id"), {"id": task_id}).fetchone()[0]:
        raise TaskError("ordinary capture cannot drop a governed assignment")
    _confirm_direct_receipts(conn, task_id)
    conn.execute(
        "UPDATE spawn_reservations SET task_relationship_generation=? WHERE key=?",
        (generation, key),
    )


TaskAlias = Literal["t", "NEW", "OLD"]


def require_if_not_discharged(task: TaskAlias, history: str) -> str:
    task_id: Literal["NEW.id", "OLD.id", "t.id"] = (
        "NEW.id" if task == "NEW" else "OLD.id" if task == "OLD" else "t.id"
    )
    return (
        f"(({history}) AND (NOT EXISTS (SELECT 1 FROM spawn_reservations n WHERE n.task_id={task_id}"
        f" AND n.task_relationship_required=0 AND n.task_relationship_generation={task}.generation)"
        f" OR ({_undischarged(task_id)})))"
    )


def relationship_history(task: TaskAlias) -> str:
    return (
        f"EXISTS (SELECT 1 FROM task_events e WHERE e.task_id={task}.id AND e.task_relationship IS NOT NULL)"
        f" OR EXISTS (SELECT 1 FROM spawn_reservations s WHERE s.task_id={task}.id AND s.task_relationship_required=1)"
    )


def relationship_required_condition(task: TaskAlias) -> str:
    return require_if_not_discharged(task, relationship_history(task))
