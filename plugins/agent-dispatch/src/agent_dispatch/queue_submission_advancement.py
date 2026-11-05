"""Fresh independent readiness and submission under the queue's original custody."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .queue_common import CompletionOutcome
from .queue_records import Status, TaskError
from .queue_submission_preparation import _encoded, _latest
from .queue_submission_readiness import _state, observe_owner_state, read_preparation

if TYPE_CHECKING:
    from .queue import TaskQueue


def _replay(
    queue: TaskQueue, conn: sqlite3.Connection, task_id: str, request_id: str,
) -> CompletionOutcome | None:
    row = _latest(conn, task_id)
    if row is None:
        raise TaskError("submission advancement requires its retained preparation")
    value = json.loads(row["submission_preparation"])
    request = value["request"]
    if request["request_id"] != request_id:
        raise TaskError("submission advancement preparation request changed")
    receipt = value.get("advancement")
    if receipt is None:
        return None
    task = queue._fetch(conn, task_id)
    if (
        receipt.get("version") != 1
        or receipt.get("state") != "submitted"
        or task is None
        or task.status not in {Status.SUBMITTED, Status.COMPLETED}
        or task.owner is not None
        or task.generation != request["generation"]
        or task.owner_session_id != request["owner_session_id"]
        or task.completed_by != request["worker_id"]
        or task.completed_at != receipt["at"]
        or queue._encode_result(task.result) != receipt["encoded_result"]
        or task.result_ref != request["result_ref"]
    ):
        raise TaskError("submission advancement receipt no longer matches its submission")
    return CompletionOutcome(task, None)


def advance(queue: TaskQueue, task_id: str, *, request_id: str) -> CompletionOutcome:
    """Not a cached readiness permit or a worker/evaluator assertion."""
    with queue._connect() as conn:
        conn.execute("BEGIN")
        replay = _replay(queue, conn, task_id, request_id)
        if replay is not None:
            return replay
    original = read_preparation(queue, task_id, request_id)
    if not original["task"]["require_verification"]:
        raise TaskError("guarded submission requires an existing independent review policy")
    observe_owner_state(task_id, original)
    ts = queue._now(None)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay(queue, conn, task_id, request_id)
        if replay is not None:
            return replay
        if _encoded(_state(queue, conn, task_id, request_id)) != _encoded(original):
            raise TaskError("submission changed during owner observation; custody retained")
        row = _latest(conn, task_id)
        value = original["preparation"]
        request = value["request"]
        encoded_result = queue._encode_result(request["result"])
        value["advancement"] = {
            "version": 1, "state": "admitting", "at": ts, "encoded_result": encoded_result,
        }
        # This marker is consumed by the task UPDATE's trigger in this transaction.
        conn.execute(
            "UPDATE task_events SET submission_preparation=? WHERE id=?",
            (_encoded(value), row["id"]),
        )
        task = queue._fetch(conn, task_id)
        outcome, wake_enqueued = queue._write_submission(
            conn, task, worker_id=request["worker_id"], encoded_result=encoded_result,
            result_ref=request["result_ref"], ts=ts,
        )
        saved = json.loads(_latest(conn, task_id)["submission_preparation"])
        if saved["advancement"]["state"] != "submitted":
            raise TaskError("submission admission was not consumed; custody retained")
        conn.execute("COMMIT")
    queue._notify_submission(outcome, wake_enqueued=wake_enqueued)
    return outcome
