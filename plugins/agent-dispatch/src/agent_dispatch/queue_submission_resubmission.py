"""Use the queue's exact rejection once; never accept caller-supplied feedback."""

from __future__ import annotations

import json
import math
import sqlite3

from .producers.evaluator import Reject
from .queue_records import TaskError
from .queue_submission_preparation import _encoded

_PROPOSAL_FIELDS = {"request_id", "result", "result_ref", "resubmission"}


def rejected_preparation(
    conn: sqlite3.Connection, previous: sqlite3.Row, request: dict,
) -> dict:
    value = json.loads(previous["submission_preparation"])
    original = value["request"]
    receipt = value.get("advancement") or {}
    if (
        type(receipt.get("version")) is not int
        or receipt["version"] != 1
        or receipt.get("state") != "submitted"
        or _encoded({k: v for k, v in original.items() if k not in _PROPOSAL_FIELDS})
        != _encoded({k: v for k, v in request.items() if k not in _PROPOSAL_FIELDS})
    ):
        raise TaskError("resubmission requires its original consumed preparation and binding")
    rows = conn.execute(
        "SELECT s.id, s.ts, s.fields, s.idempotency_key, e.id AS event_id, e.note"
        " FROM task_steer s JOIN task_events e ON e.task_id=s.task_id AND e.ts=s.ts"
        " WHERE s.task_id=? AND s.sender='evaluator' AND s.idempotency_key LIKE 'reject:%'"
        " AND e.id>? AND e.from_status='submitted' AND e.to_status='started'"
        " AND e.worker='evaluator' ORDER BY e.id DESC, s.id DESC LIMIT 2",
        (request["task_id"], previous["id"]),
    ).fetchall()
    if len(rows) != 1:
        raise TaskError("resubmission requires one authoritative rejection of its proposal")
    row = rows[0]
    try:
        rejection = json.loads(row["fields"])["verification_rejection"]
        decision = Reject(rejection["reason"], rejection["feedback"])
        submission = rejection["submission"]
        updated = submission["updated_at"]
        expected = {
            "generation": original["generation"],
            "owner_session_id": original["owner_session_id"],
            "result": original["result"],
            "result_ref": original["result_ref"],
            "completed_by": original["worker_id"],
            "completed_at": receipt["at"],
        }
        if (
            _encoded({key: submission[key] for key in expected}) != _encoded(expected)
            or type(updated) not in (int, float)
            or not math.isfinite(updated)
            or row["idempotency_key"] != f"reject:{original['generation']}:{updated!r}"
            or row["note"] != "submission rejected: " + decision.reason
        ):
            raise ValueError("rejection does not match the consumed proposal")
        return {
            "previous_preparation_event_id": previous["id"],
            "rejection_steer_id": row["id"],
            "rejection_event_id": row["event_id"],
            "reason": decision.reason,
            "feedback": decision.feedback,
        }
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise TaskError("resubmission rejection is changed or unconfirmed") from exc


def retained_lineage(conn: sqlite3.Connection, request: dict) -> dict | None:
    lineage = request.get("resubmission")
    if lineage is None:
        return None
    if (
        not isinstance(lineage, dict)
        or type(lineage.get("previous_preparation_event_id")) is not int
    ):
        raise TaskError("resubmission lineage is invalid")
    previous = conn.execute(
        "SELECT id, submission_preparation FROM task_events"
        " WHERE id=? AND task_id=? AND submission_preparation IS NOT NULL",
        (lineage["previous_preparation_event_id"], request["task_id"]),
    ).fetchone()
    if previous is None or _encoded(rejected_preparation(conn, previous, request)) != _encoded(lineage):
        raise TaskError("resubmission rejection lineage changed; custody retained")
    return lineage
