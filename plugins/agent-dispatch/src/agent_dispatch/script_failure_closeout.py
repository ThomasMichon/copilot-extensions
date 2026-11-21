"""Conclude one exited deterministic body. A worker's failure report is not exit evidence."""

from __future__ import annotations

import json
import sqlite3
import httpx
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import spawn_script_body as scripts
from .client_errors import DispatchError
from .managed_retention import process_identity_domain
from .queue_common import CompletionOutcome, Task
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_execution_wakes import require_no_wake_for_release
from .queue_records import Status, TaskError
from .queue_submission_preparation import require_unprepared_submission
from .reservation_assignment import retains_assignment

if TYPE_CHECKING:
    from .queue import TaskQueue


class ScriptExit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reservation_key: str = Field(min_length=1)
    session_handle: str = Field(min_length=1, max_length=16384)
    worker_id: str = Field(min_length=1, max_length=512)
    generation: int = Field(ge=0)
    status: str
    owner_session_id: str | None
    startup_revision: int = Field(ge=1)


def _snapshot(queue: TaskQueue, conn: sqlite3.Connection, task_id: str,
              expected: ScriptExit) -> tuple[Task, sqlite3.Row]:
    task = queue._fetch(conn, task_id)
    res = conn.execute("SELECT * FROM spawn_reservations WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                       (task_id,)).fetchone()
    parsed = scripts._parse_script_body_handle(expected.session_handle)
    if not task or not res or not parsed or (
        expected.status not in Status.HELD or expected.worker_id != parsed[0]
        or res["key"] != expected.reservation_key or res["session_handle"] != expected.session_handle
        or res["startup_revision"] != expected.startup_revision
        or task.generation != expected.generation or task.owner_session_id != expected.owner_session_id
    ):
        raise TaskError("script closeout identity changed")
    return task, res


def _replayed(task: Task, res: sqlite3.Row, expected: ScriptExit) -> bool:
    if task.status != Status.ABANDONED or res["state"] != "settled":
        return False
    receipt = json.loads(res["conclusion_detail"] or "null")
    return bool(isinstance(receipt, dict) and receipt.get("script_exit_v1") == expected.model_dump()
                and task.owner is None and res["release_requested"] and res["conclusion_state"] == "complete")


def _owned(queue: TaskQueue, conn: sqlite3.Connection, task: Task, res: sqlite3.Row, expected: ScriptExit) -> None:
    if (
        task.status != expected.status or task.owner != expected.worker_id
        or task.kind == "standing-charter" or task.hold_reason is not None or task.awaiting_steer
        or task.hold_at is not None or task.hold_actor is not None
        or res["state"] != "spawned" or not res["startup_complete"] or res["startup_fenced"]
        or res["release_requested"] or res["native_release_pending"] or res["cooling_token"]
        or res["conclusion_state"] or res["agent_info"] or res["worktree"] or res["allocation_project"]
        or res["driver"] not in (None, "agent-dispatch") or res["task_relationship_required"]
        or conn.execute("SELECT 1 FROM task_pool_assignments WHERE task_id=? AND assignment_released=0",
                        (task.id,)).fetchone()
        or conn.execute("SELECT 1 FROM task_relevance_closeouts WHERE task_id=?", (task.id,)).fetchone()
        or conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND task_relationship IS NOT NULL",
                        (task.id,)).fetchone()
        or conn.execute("SELECT 1 FROM acquisition_receipts WHERE reservation_key=:key"
                        " UNION ALL SELECT 1 FROM acquisition_markers WHERE reservation_key=:key"
                        " UNION ALL SELECT 1 FROM pool_reservation_bindings WHERE reservation_key=:key LIMIT 1",
                        {"key": expected.reservation_key}).fetchone()
    ):
        raise TaskError("script closeout no longer owns an unheld headless attempt")
    require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
    require_unprepared_submission(conn, task.id)
    require_no_wake_for_release(queue, conn, res)


def finish(queue: TaskQueue, task_id: str, expected: ScriptExit) -> CompletionOutcome:
    with queue._connect() as conn:
        conn.execute("BEGIN")
        task, res = _snapshot(queue, conn, task_id, expected)
        if _replayed(task, res, expected):
            return CompletionOutcome(task, None)
        _owned(queue, conn, task, res, expected)
    parsed = scripts._parse_script_body_handle(expected.session_handle)
    if not parsed:
        raise TaskError("invalid script handle")
    _, pid, start, _ = parsed
    handle = json.loads(expected.session_handle.partition(":")[2])
    domain = handle.get("process_domain")
    if (handle.get("task_id") != task_id or type(handle.get("queued_generation")) is not int
            or handle["queued_generation"] + 1 != expected.generation):
        raise TaskError("script closeout lacks original claim generation")
    try:
        gone = bool(domain and process_identity_domain() == domain and scripts.exact_script_exit(pid, start))
    except (OSError, RuntimeError) as exc:
        raise TaskError("script process owner unavailable") from exc
    if not gone:
        raise TaskError("script process exit is unconfirmed in its original PID domain")

    def retain(conn: sqlite3.Connection) -> None:
        current, current_res = _snapshot(queue, conn, task_id, expected)
        _owned(queue, conn, current, current_res, expected)
        if dict(current_res) != dict(res) or current.latest_progress != task.latest_progress:
            raise TaskError("script reservation changed during exit observation")
        conn.execute("UPDATE spawn_reservations SET state='settled',release_requested=1,"
                     "conversation_retired=1,conclusion_state='complete',conclusion_detail=?,"
                     "detail=?,updated_at=? WHERE key=?",
                     (json.dumps({"script_exit_v1": expected.model_dump()}), reason, ts,
                      expected.reservation_key))
        if retains_assignment(conn, task_id):
            raise TaskError("other reservation custody excludes script failure closeout")

    ts = queue._now(None)
    beat = json.loads(task.latest_progress or "null")
    reason = (beat["summary"] if isinstance(beat, dict) and beat.get("phase") == "script-failure"
              and isinstance(beat.get("summary"), str) else "script body exited without successful completion")
    try:
        outcome = queue._transition(task_id, allowed={expected.status}, to=Status.ABANDONED,
                                    worker_id=expected.worker_id, extra={"owner": None, "lease_expires_at": None},
                                    note=reason, now=ts, expected_generation=expected.generation,
                                    expected_owner_session_id=expected.owner_session_id,
                                    expected_status=expected.status, reject_if_held=True, transaction_guard=retain)
    except TaskError:
        with queue._connect() as conn:
            conn.execute("BEGIN")
            current, current_res = _snapshot(queue, conn, task_id, expected)
            if _replayed(current, current_res, expected):
                return CompletionOutcome(current, None)
        raise
    if not isinstance(outcome, Task):
        raise TaskError("unconfirmed script closeout")
    return CompletionOutcome(outcome, "task.abandoned")


def register(app: FastAPI, queue: TaskQueue, guard: Callable[..., dict[str, Any]],
             control: Callable[..., None]) -> None:
    @app.post("/tasks/{task_id}/script-exit", dependencies=[Depends(control)])
    def close(task_id: str, body: ScriptExit, request: Request) -> dict[str, Any]:
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="coordinator draining")
            return guard(lambda: finish(queue, task_id, body))


def recover(client: Any, reservation: dict[str, Any], task: dict[str, Any], worker_id: str) -> bool:
    if not callable(getattr(client, "close_failed_script", None)):
        return False
    try:
        value = client.close_failed_script(
            task["id"], reservation_key=reservation["key"], session_handle=reservation["session_handle"],
            worker_id=worker_id, generation=task["generation"], status=task["status"],
            owner_session_id=task.get("owner_session_id"), startup_revision=reservation["startup_revision"],
        )
    except (DispatchError, httpx.TransportError, OSError, ValueError):
        return False
    return bool(isinstance(value, dict) and value.get("id") == task["id"]
                and value.get("status") == Status.ABANDONED and value.get("generation") == task["generation"])
