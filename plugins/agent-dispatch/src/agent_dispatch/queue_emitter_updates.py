"""Local storage step for a producer-authorized change to a queued task.

The caller owns producer authentication and observation ordering; this is not
a client endpoint or a change to ordinary create/dedup behavior.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .queue_common import Task
from .queue_dedup import find_live_dedup_task
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_execution_handoff import require_execution_admission
from .queue_producer_fences import ProducerFenceMixin
from .queue_records import Status, TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue

_CURSOR_QUERY = "SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id=?"


@dataclass(frozen=True)
class QueuedObservation:
    task_id: str
    repo: str
    key: str
    source: str
    origin: str
    last_event: int


def _cursor(conn: sqlite3.Connection, task_id: str) -> int:
    return int(conn.execute(_CURSOR_QUERY, (task_id,)).fetchone()[0])


def _queued(
    conn: sqlite3.Connection, *, task_id: str, repo: str, key: str, source: str, origin: str,
) -> Task:
    task = find_live_dedup_task(conn, repo, key)
    if (not task or not all((repo, key, source, origin)) or task.id != task_id
            or task.source != source or task.origin_ref != origin):
        raise TaskError("emitter observation does not match the original task")
    if (task.status != Status.QUEUED or task.kind != "finite" or task.owner or task.owner_session_id
            or task.hold_reason is not None or task.payload_ref is not None or task.awaiting_steer):
        raise TaskError("emitter observation requires an available queued inline task")
    if task.attempts or task.generation:
        raise TaskError("previously assigned tasks require an update for the original worker")
    if (task.producer_fence is not None
            or ProducerFenceMixin._producer_scope_row(conn, repo=repo, source=source) is not None
            or ProducerFenceMixin._required_label_scope_rows(conn, labels=task.labels)):
        raise TaskError("managed emitter observations require authenticated producer admission")
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? LIMIT 1", (task_id,),
    ).fetchone():
        raise TaskError("queued emitter update requires a task without reservation history")
    require_execution_admission(conn, task_id)
    require_execution_send_reconciliation(conn, task_id, exclusive_key=task.exclusive_key)
    return task


def observe_queued(
    queue: TaskQueue, task_id: str, *, repo: str, key: str, source: str, origin: str,
) -> QueuedObservation:
    with queue._connect() as conn:
        conn.execute("BEGIN")
        _queued(conn, task_id=task_id, repo=repo, key=key, source=source, origin=origin)
        return QueuedObservation(task_id, repo, key, source, origin, _cursor(conn, task_id))


def update_queued(queue: TaskQueue, basis: QueuedObservation, payload: str) -> bool:
    """Replace only inline evidence; retain identity, objective and scheduling fields."""
    if not isinstance(payload, str) or queue._payload_needs_spill(None, payload):
        raise TaskError("emitter observation exceeds the inline payload contract")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = _queued(conn, task_id=basis.task_id, repo=basis.repo, key=basis.key,
                       source=basis.source, origin=basis.origin)
        if task.payload_inline == payload:
            return False
        if basis.last_event != _cursor(conn, task.id):
            raise TaskError("queued emitter observation changed; reconcile the original request")
        ts = queue._now(None)
        conn.execute("UPDATE tasks SET payload_inline=?,updated_at=? WHERE id=?", (payload, ts, task.id))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        queue._audit(conn, task.id, ts=ts, from_status=task.status, to_status=task.status,
                     note=f"emitter observation updated: sha256:{digest}")
        return True
