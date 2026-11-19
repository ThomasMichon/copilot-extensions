"""Record an evaluator's disputed closeout without re-executing a retired goal."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .producers.evaluator import Reject
from .queue_common import CompletionOutcome, Task
from .queue_execution_handoff import closed_execution
from .queue_records import Status, TaskError
from .queue_relevance_acknowledgement import _replay
from .review_closeout_submission import SUBMIT

if TYPE_CHECKING:
    from .queue import TaskQueue


def record(queue: TaskQueue, conn: sqlite3.Connection, task: Task, decision: Reject, key: str, ts: float,
           *, generation: int, native: str, worker: str, updated_at: float) -> CompletionOutcome | None:
    row = conn.execute("SELECT acknowledgement FROM task_relevance_closeouts WHERE task_id=?", (task.id,)).fetchone()
    if not row or not row[0]:
        return None
    receipt = json.loads(row[0])
    if receipt.get("finish_action") != SUBMIT:
        return None
    if (task.status != Status.SUBMITTED or task.generation != generation or task.owner_session_id != native
            or task.completed_by != worker or task.updated_at != updated_at or task.hold_reason is not None
            or closed_execution(conn, task.id) is not None):
        raise TaskError("finalized review submission changed or is held")
    if _replay(queue, conn, task.id, receipt["arguments"]) is None:
        raise TaskError("finalized review submission lacks an acknowledgement")
    fields = json.dumps({"closeout_verification_rejection": {
        "reason": decision.reason, "feedback": decision.feedback,
        "request_id": receipt["arguments"]["request_id"], "generation": generation,
        "completed_by": worker, "owner_session_id": native, "completed_at": task.completed_at,
    }}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    identity = "closeout:" + key
    prior = conn.execute("SELECT fields FROM task_steer WHERE task_id=? AND idempotency_key=?",
                         (task.id, identity)).fetchone()
    if prior:
        if prior[0] != fields:
            raise TaskError("conflicting evaluator rejection of the finalized review submission")
        return CompletionOutcome(task, None)
    conn.execute("INSERT INTO task_steer(task_id,ts,fields,sender,idempotency_key) VALUES (?,?,?,'evaluator',?)",
                 (task.id, ts, fields, identity))
    queue._audit(conn, task.id, ts=ts, from_status=task.status, to_status=task.status,
                 worker="evaluator", note="finalized review submission disputed; no execution wake")
    return CompletionOutcome(task, "task.closeout_disputed")
