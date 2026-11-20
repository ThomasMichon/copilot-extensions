"""Original-worker submission after independently verified PR workspace finalization."""

from __future__ import annotations

import sqlite3
import hashlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .queue_common import CompletionOutcome, Task
from .queue_records import Status, TaskError
from .queue_submission_preparation import _encoded, _latest, previous_for_assignment, require_request_slot
from .queue_submission_resubmission import rejected_preparation, retained_lineage

if TYPE_CHECKING:
    from .queue import TaskQueue

SUBMIT = "submit-finalized-review/v1"
ABANDON = "abandon-finalized-source/v1"


def eligible(task: Task | None) -> bool:
    return bool(task and task.status in {Status.STARTED, Status.SUSPENDED}
                and task.owner and task.owner_session_id and task.require_verification
                and task.evaluator_ref and "pr-review" in task.labels)


def _proposal(state: dict[str, Any]) -> dict[str, Any]:
    args = state["arguments"]
    request = state["relationship"]["request"]
    return {
        "request_id": "closeout:" + hashlib.sha256(args["request_id"].encode()).hexdigest(),
        "task_id": request["task_id"], "queue_identity": state["binding"]["queue_identity"],
        "worker_id": args["worker_id"], "generation": args["expected_generation"],
        "owner_session_id": args["native_id"], "workspace": request["workspace"],
        "assignment_event_id": request["event_id"], "assignment_request_id": request["request_id"],
        "assignment_continuation": state["relationship"].get("continuation"),
        "result": None, "result_ref": f"relevance-closeout:{args['request_id']}",
    }


def _same_proposal(conn: sqlite3.Connection, state: dict[str, Any], value: dict[str, Any]) -> bool:
    if value.get("advancement") is not None:
        return False
    expected = _proposal(state)
    request = value["request"]
    retained_lineage(conn, request)
    return (all(_encoded(request.get(k)) == _encoded(v) for k, v in expected.items()
                    if k not in {"request_id", "result", "result_ref"})
            and request.get("expected_status", state["status"]) == state["status"])


def prepare(queue: TaskQueue, conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    """Retain the original worker's proposal before its independently checked claim is settled."""
    task = queue._fetch(conn, state["relationship"]["request"]["task_id"])
    if not eligible(task) or task is None:
        return
    if task.awaiting_steer:
        raise TaskError("resolve outstanding steering request before review closeout")
    proposal = _proposal(state)
    prior = previous_for_assignment(conn, task.id, proposal["assignment_event_id"])
    if prior is not None:
        value = json.loads(prior["submission_preparation"])
        if _same_proposal(conn, state, value):
            return
        if value.get("advancement") is None:
            raise TaskError("another retained submission exists; reconcile before review closeout")
        proposal["resubmission"] = rejected_preparation(conn, prior, proposal)
    if state["relationship"]["state"] == "closed":
        saved = conn.execute("SELECT readiness FROM task_relevance_closeouts WHERE task_id=?", (task.id,)).fetchone()
        ready = json.loads(saved["readiness"]) if saved and saved["readiness"] else {}
        if (ready.get("arguments") != state["arguments"] or ready.get("binding") != state["binding"]
                or ready.get("observation", {}).get("ready") is not True):
            raise TaskError("legacy closed assignment lacks its original readiness receipt")
    require_request_slot(conn, task.id, proposal["request_id"], prior)
    conn.execute(
        "INSERT INTO task_events(task_id,ts,from_status,to_status,worker,note,submission_preparation)"
        " VALUES (?,?,?,?,?,?,?)", (task.id, queue._now(None), task.status, task.status, task.owner,
        "review closeout submission prepared", _encoded({"version": 1, "request": proposal, "blocker": None})),
    )


def submit(queue: TaskQueue, task_id: str, *, worker_id: str, ts: float, original: dict[str, Any],
           retain: Callable[[sqlite3.Connection], None]) -> CompletionOutcome:
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = queue._fetch(conn, task_id)
        if not eligible(task) or task is None or task.owner != worker_id:
            raise TaskError("finalized review submission requires the original accepted worker")
        retain(conn)
        prior = _latest(conn, task_id)
        value = json.loads(prior["submission_preparation"]) if prior else {}
        if prior is None or not _same_proposal(conn, original, value) or task.awaiting_steer:
            raise TaskError("finalized review submission lacks its original pending proposal")
        encoded_result = queue._encode_result(value["request"]["result"])
        value["advancement"] = {"version": 1, "state": "admitting", "at": ts, "encoded_result": encoded_result}
        conn.execute("UPDATE task_events SET submission_preparation=? WHERE id=?", (_encoded(value), prior["id"]))
        outcome, wake = queue._write_submission(
            conn, task, worker_id=worker_id, encoded_result=encoded_result,
            result_ref=value["request"]["result_ref"], ts=ts,
        )
        saved = _latest(conn, task_id)
        if saved is None or json.loads(saved["submission_preparation"])["advancement"]["state"] != "submitted":
            raise TaskError("review submission proposal not consumed; custody retained")
        conn.commit()
    queue._notify_submission(outcome, wake_enqueued=wake)
    return outcome
