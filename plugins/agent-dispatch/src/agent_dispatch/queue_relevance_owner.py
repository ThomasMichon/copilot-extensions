"""Make the body that accepts an obsolete task its relevance closeout owner."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .queue_records import Status


def bind_closeout_owner(owner: Any, conn: sqlite3.Connection, task_id: str, ts: float) -> bool:
    """Rebind a pending closeout to the started body and wake it inside this transaction.

    Relevance no longer blocks start, retry or resume. Whichever body accepts the
    task (including a resumed or replacement native session) receives the original
    closeout steer and a closeout wake, then closes out itself. Returns whether a
    wake was enqueued so the caller notifies delivery after commit.
    """
    row = conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=? AND acknowledgement IS NULL",
        (task_id,),
    ).fetchone()
    if row is None:
        return False
    task = owner._fetch(conn, task_id)
    if (
        task is None or task.status != Status.STARTED or task.hold_reason is not None
        or not task.owner or not task.owner_session_id
    ):
        return False
    if (row["generation"], row["owner"], row["owner_session_id"]) != (
        task.generation, task.owner, task.owner_session_id,
    ):
        from .queue_relevance_binding import capture_binding
        conn.execute(
            "UPDATE task_relevance_closeouts SET generation=?, owner=?, owner_session_id=?,"
            " resource_binding=?, readiness=NULL WHERE task_id=?",
            (
                task.generation, task.owner, task.owner_session_id,
                json.dumps(capture_binding(conn, task), sort_keys=True, separators=(",", ":")),
                task_id,
            ),
        )
        # A wake delivered to an earlier body is not this body's closeout request.
        conn.execute(
            "UPDATE task_steer SET wake_operation_id=NULL, taken=0, taken_at=NULL WHERE id=?", (row["steer_id"],),
        )
        row = conn.execute(
            "SELECT * FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
        ).fetchone()
    from .queue_relevance_delivery import enqueue_closeout
    return enqueue_closeout(owner, conn, task, row, ts=ts) is not None


NO_WORKER_FINISH_ACTION = "relevance-no-worker/v1"


def settle_unowned_closeout(owner: Any, conn: sqlite3.Connection, task_id: str, ts: float) -> bool:
    """Abandon a pending closeout once its last acquisition retired without a worker.

    A failed or exhausted startup leaves no body to close out; the closeout was
    pending only because the acquisition outcome was unknown. Once no live
    custody remains, the task is abandoned like any resource-free obsolete task.
    """
    from .queue_relevance import _custody
    row = conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=? AND acknowledgement IS NULL",
        (task_id,),
    ).fetchone()
    task = owner._fetch(conn, task_id) if row is not None else None
    if (
        task is None or task.hold_reason is not None or task.kind != "finite"
        or task.status not in {Status.PROPOSED, Status.QUEUED, Status.CLAIMED}
        or _custody(conn, task)
    ):
        return False
    receipt = {
        "task_id": task_id, "finish_action": NO_WORKER_FINISH_ACTION, "at": ts,
        "process_retirement": False,
        "task_attachment": {"generation": task.generation, "owner_session_id": None},
    }
    conn.execute(
        "UPDATE task_relevance_closeouts SET acknowledgement=? WHERE task_id=?",
        (json.dumps(receipt, sort_keys=True, separators=(",", ":")), task_id),
    )
    conn.execute(
        "UPDATE tasks SET status=?, owner=NULL, owner_session_id=NULL, lease_expires_at=NULL,"
        " activity=NULL, activity_updated_at=?, updated_at=? WHERE id=?",
        (Status.ABANDONED, ts, ts, task_id),
    )
    owner._audit(
        conn, task_id, ts=ts, from_status=task.status, to_status=Status.ABANDONED,
        note="source relevance settled: acquisition retired without a worker",
    )
    return True


def relevance_abandoned(conn: sqlite3.Connection, task: Any) -> bool:
    """Whether this exact incarnation was abandoned by an acknowledged relevance closeout."""
    if task.status != Status.ABANDONED:
        return False
    row = conn.execute(
        "SELECT acknowledgement FROM task_relevance_closeouts WHERE task_id=?", (task.id,),
    ).fetchone()
    if row is None or row["acknowledgement"] is None:
        return False
    try:
        attachment = json.loads(row["acknowledgement"]).get("task_attachment") or {}
    except (TypeError, ValueError, AttributeError):
        return False
    return bool(attachment.get("generation") == task.generation)
