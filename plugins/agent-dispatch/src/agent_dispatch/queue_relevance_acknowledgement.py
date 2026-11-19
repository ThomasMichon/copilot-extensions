"""Observe actual workspace finalization before the existing terminal transition."""

from __future__ import annotations

from collections.abc import Callable
import json
import math
import sqlite3
from typing import TYPE_CHECKING, Any

from .queue_common import CompletionOutcome, Task, _task_transition_spec
from .queue_records import Status, TaskError
from .queue_relevance_readiness import _state
from .queue_task_relationships import _encoded
from . import review_closeout_submission as submission

if TYPE_CHECKING:
    from .queue import TaskQueue


def _replay(
    queue: TaskQueue, conn: sqlite3.Connection, task_id: str, arguments: dict[str, Any],
) -> CompletionOutcome | None:
    row = conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
    ).fetchone()
    if row is None or row["acknowledgement"] is None:
        return None
    receipt = json.loads(row["acknowledgement"])
    task = queue._fetch(conn, task_id)
    submitted = receipt.get("finish_action") == submission.SUBMIT
    if (
        type(arguments["expected_generation"]) is not int
        or receipt["arguments"] != arguments or receipt["task_id"] != task_id
        or receipt["binding"] != json.loads(row["resource_binding"])
        or task is None or task.owner is not None
        or task.generation != arguments["expected_generation"]
        or {
            "generation": task.generation, "owner_session_id": task.owner_session_id,
        } != receipt["task_attachment"]
        or (not submitted and (task.status != Status.ABANDONED or task.updated_at != receipt["at"]))
        or (submitted and (task.status not in {Status.SUBMITTED, Status.COMPLETED, Status.ABANDONED}
                           or task.completed_at != receipt["at"] or task.completed_by != arguments["worker_id"]
                           or task.result_ref != f"relevance-closeout:{arguments['request_id']}"))
    ):
        raise TaskError("relevance closeout acknowledgement incarnation changed; custody retained")
    return CompletionOutcome(task, None)


def _prepared(queue: TaskQueue, conn: sqlite3.Connection, task_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(queue, conn, task_id, arguments)
    row = conn.execute(
        "SELECT readiness FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
    ).fetchone()
    ready = json.loads(row["readiness"]) if row["readiness"] is not None else None
    if (
        ready is None or ready["arguments"] != arguments or ready["binding"] != state["binding"]
        or state["relationship"]["state"] != "closed"
    ):
        raise TaskError("relevance closeout requires readiness and its settled original claim")
    task = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    # Liveness can advance while the independent workspace owner is observed.
    for key in (
        "activity", "activity_updated_at", "lease_expires_at", "updated_at",
        "last_seen_at", "latest_progress",
    ):
        task.pop(key, None)
    return {**state, "readiness": ready, "task": task}


def finish(
    queue: TaskQueue, task_id: str, *,
    submit_review: bool = False, authority_guard: Callable[[], None] | None = None, **arguments: str | int,
) -> CompletionOutcome:
    """Internal receiver: no caller-supplied finalization or terminal proof."""
    if authority_guard is not None:
        authority_guard()
    with queue._connect() as conn:
        conn.execute("BEGIN")
        replay = _replay(queue, conn, task_id, arguments)
        if replay is not None:
            return replay
        original = _prepared(queue, conn, task_id, arguments)
        if submit_review != submission.eligible(queue._fetch(conn, task_id)):
            raise TaskError("original-worker closeout disposition changed; use the negotiated finish action")
    from .pool_decline_observation import observe

    try:
        observation = observe(
            original["task"], original["allocation"], {"native_id": arguments["native_id"]},
        )
        if observation.get("creation_nonce") != original["binding"]["workspace"]["creation_nonce"]:
            raise TaskError(
                "relevance closeout finalized workspace incarnation changed; custody retained",
            )
    except TaskError:
        with queue._connect() as conn:
            conn.execute("BEGIN")
            replay = _replay(queue, conn, task_id, arguments)
            if replay is not None:
                return replay
        raise
    ts = queue._now(None)
    if not math.isfinite(ts):
        raise TaskError("relevance closeout requires a finite acknowledgement time")
    receipt = {
        "task_id": task_id, "arguments": arguments, "binding": original["binding"],
        "observation": observation, "at": ts, "process_retirement": False,
        "task_attachment": {
            "generation": original["task"]["generation"],
            "owner_session_id": original["task"]["owner_session_id"],
        },
    }

    if submit_review:
        receipt["finish_action"] = submission.SUBMIT

    def retain(conn: sqlite3.Connection) -> None:
        if authority_guard is not None:
            authority_guard()
        if _encoded(_prepared(queue, conn, task_id, arguments)) != _encoded(original):
            raise TaskError(
                "relevance closeout changed during finalized observation; custody retained",
            )
        conn.execute(
            "UPDATE task_relevance_closeouts SET acknowledgement=? WHERE task_id=?",
            (_encoded(receipt), task_id),
        )

    allowed, to = _task_transition_spec("abandon")
    try:
        if submit_review:
            return submission.submit(queue, task_id, worker_id=str(arguments["worker_id"]),
                                     original=original, ts=ts, retain=retain)
        task = queue._transition(
            task_id, allowed=allowed, to=to, worker_id=str(arguments["worker_id"]),
            require_owner=False, now=ts, note="source relevance owner closeout acknowledged",
            extra={"owner": None, "lease_expires_at": None},
            expected_generation=receipt["task_attachment"]["generation"],
            expected_owner_session_id=receipt["task_attachment"]["owner_session_id"],
            expected_status=original["task"]["status"], reject_if_held=True,
            transaction_guard=retain,
            unaccepted_closeout=original["binding"].get("owner_phase") == "before_start",
        )
    except TaskError:
        with queue._connect() as conn:
            conn.execute("BEGIN")
            replay = _replay(queue, conn, task_id, arguments)
            if replay is not None:
                return replay
        raise
    assert isinstance(task, Task)
    return CompletionOutcome(task, "task.abandoned")
