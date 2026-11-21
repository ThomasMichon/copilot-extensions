"""Requeue the observed legacy incarnation, never merely the current worker of a task."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Literal, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .bridge_remote import normalize_host
from .client_errors import DispatchError
from .queue import TaskQueue
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_execution_handoff import require_execution_admission
from .queue_execution_wakes import require_no_wake_for_release
from .queue_records import Status, TaskError
from .queue_submission_preparation import require_unprepared_submission
from .reservation_assignment import retains_assignment
from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    supervisor: str
    key: str
    status: Literal["claimed", "started", "suspended"]
    generation: int
    owner: str
    native: str | None
    session_handle: str
    worktree: str | None
    startup_revision: int
    state: Literal["cold", "spawned"]
    updated_at: float
    task_updated_at: float
    wake_id: str | None
    wake_status: str | None
    productive: bool


def _check(queue: TaskQueue, conn: sqlite3.Connection, task_id: str, obs: Observation) -> sqlite3.Row:
    task = queue._fetch(conn, task_id)
    row = conn.execute("SELECT * FROM spawn_reservations WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                       (task_id,)).fetchone()
    if (not task or not row or (task.status, task.generation, task.owner, task.owner_session_id)
            != (obs.status, obs.generation, obs.owner, obs.native)
            or (task.wake_operation_id, task.wake_status) != (obs.wake_id, obs.wake_status)
            or (row["key"], row["startup_revision"], row["session_handle"], row["worktree"])
            != (obs.key, obs.startup_revision, obs.session_handle, obs.worktree)
            or task.hold_reason is not None or task.awaiting_steer or task.kind == "standing-charter"
            or not row["startup_complete"] or row["cooling_token"] or row["native_release_pending"]
            or not obs.supervisor or row["reserved_by"] not in (None, obs.supervisor)
            or not obs.owner or not obs.session_handle or row["driver"] not in (None, "cli", "agent-dispatch")
            or row["agent_info"]
            or obs.session_handle.startswith("script-body:") or row["task_relationship_required"]
            or row["task_relationship_event_id"] is not None
            or conn.execute("SELECT 1 FROM spawn_reservations WHERE task_id=? AND key<>? AND "
                            "(state IN ('reserving','spawned','cold','releasing') OR native_release_pending=1 "
                            "OR conclusion_state IN ('pending','held'))", (task_id, obs.key)).fetchone()
            or conn.execute("SELECT 1 FROM task_pool_assignments WHERE task_id=? AND assignment_released=0",
                            (task_id,)).fetchone()
            or conn.execute("SELECT 1 FROM pool_reservation_bindings WHERE reservation_key=?", (obs.key,)).fetchone()
            or conn.execute("SELECT 1 FROM acquisition_receipts WHERE reservation_key=:key UNION ALL "
                            "SELECT 1 FROM acquisition_markers WHERE reservation_key=:key", {"key": obs.key}).fetchone()
            or conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND task_relationship IS NOT NULL",
                            (task_id,)).fetchone()):
        raise TaskError("gone recovery has a different or protected incarnation")
    hosted = _parse_local_body_handle(obs.session_handle) or _parse_fleet_body_handle(obs.session_handle)
    if not hosted and (not obs.worktree or obs.owner.partition("/")[2] != obs.worktree
                       or row["driver"] == "cli" and obs.native != obs.session_handle):
        raise TaskError("worktree verdict does not identify this reservation owner")
    require_execution_admission(conn, task_id)
    require_execution_send_reconciliation(conn, task_id, exclusive_key=task.exclusive_key)
    require_unprepared_submission(conn, task_id)
    require_no_wake_for_release(queue, conn, row)
    return cast(sqlite3.Row, row)


def finish(queue: TaskQueue, task_id: str, obs: Observation) -> None:
    def guard(conn: sqlite3.Connection) -> None:
        row = _check(queue, conn, task_id, obs)
        if (row["state"] != ("settled" if obs.productive or obs.state == "cold" else "failed")
                or not row["release_requested"]
                or (obs.state == "cold" or row["worktree_ownership"] == "created")
                and row["conclusion_state"] != "complete"
                or retains_assignment(conn, task_id)
                or not conn.execute("DELETE FROM gone_owner_requeues WHERE key=? AND observation=?",
                                    (obs.key, obs.model_dump_json())).rowcount):
            raise TaskError("gone recovery awaits original retirement and cleanup")

    queue._transition(task_id, allowed={obs.status}, to=Status.QUEUED, worker_id=obs.owner,
                      expected_generation=obs.generation, expected_owner_session_id=obs.native,
                      expected_status=obs.status, reject_if_held=True, transaction_guard=guard,
                      bump_generation=True, note="confirmed gone owner retired; requeued for re-embodiment",
                      extra={"owner": None, "owner_session_id": None, "lease_expires_at": None,
                             "claimed_at": None, "last_liveness": None, "resume_requested": 0,
                             "monitor_kind": None, "monitor_not_before": None})


def begin(queue: TaskQueue, task_id: str, obs: Observation) -> None:
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _check(queue, conn, task_id, obs)
        task = queue._fetch(conn, task_id)
        if conn.execute("SELECT 1 FROM gone_owner_requeues WHERE key=? AND observation=?",
                        (obs.key, obs.model_dump_json())).fetchone():
            return
        if (not task or task.updated_at != obs.task_updated_at or row["state"] != obs.state
                or row["updated_at"] != obs.updated_at or row["release_requested"]
                or row["conclusion_state"] or row["startup_fenced"]
                or (obs.state == "cold") != (obs.status == Status.SUSPENDED)
                or obs.state == "cold" and (not task.resume_requested or not _parse_local_body_handle(obs.session_handle))):
            raise TaskError("gone recovery observation changed")
        conn.execute("INSERT INTO gone_owner_requeues(key,task_id,supervisor,observation) VALUES (?,?,?,?)",
                     (obs.key, task_id, obs.supervisor, obs.model_dump_json()))
        disposition = "settled" if obs.productive or obs.state == "cold" else "failed"
        deferred = obs.state == "cold" or row["worktree_ownership"] == "created"
        conn.execute("UPDATE spawn_reservations SET state=?,release_requested=1,release_disposition=?,"
                     "conclusion_state=?,detail=?,updated_at=? WHERE key=?",
                     ("releasing" if deferred else disposition, disposition, "pending" if deferred else None,
                      "gone owner recovery", queue._now(None), obs.key))
    if not deferred:
        finish(queue, task_id, obs)


def reconcile(queue: TaskQueue, supervisor: str) -> int:
    # Only retained proposals with terminal reservations are candidates, never a history scan.
    with queue._connect() as conn:
        rows = conn.execute("SELECT p.* FROM gone_owner_requeues p JOIN spawn_reservations r ON r.key=p.key"
                            " JOIN tasks t ON t.id=p.task_id WHERE p.supervisor=? AND r.state IN ('failed','settled')"
                            " AND t.generation=json_extract(p.observation,'$.generation') AND t.hold_reason IS NULL"
                            " AND t.status=json_extract(p.observation,'$.status')"
                            " AND NOT EXISTS (SELECT 1 FROM task_relevance_closeouts c WHERE c.task_id=t.id)"
                            " AND COALESCE(r.conclusion_state,'') NOT IN ('held','pending')", (supervisor,)).fetchall()
    done = 0
    for row in rows:
        try:
            finish(queue, row["task_id"], Observation.model_validate_json(row["observation"]))
            done += 1
        except TaskError:
            continue
    return done


def register(app: FastAPI, queue: TaskQueue, control: Callable[..., None]) -> None:
    @app.post("/tasks/{task_id}/gone-owner-requeue", dependencies=[Depends(control)])
    def recover(task_id: str, body: Observation, request: Request) -> dict[str, bool]:
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="coordinator draining")
            try:
                begin(queue, task_id, body)
            except TaskError as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            return {"accepted": True}

    @app.post("/spawn-reservations/reconcile-gone-requeues", dependencies=[Depends(control)])
    def resume_dispositions(supervisor: str, request: Request) -> dict[str, int]:
        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, detail="coordinator draining")
            return {"requeued": reconcile(queue, supervisor)}


def request(supervisor: Any, reservation: dict[str, Any], task: dict[str, Any], productive: bool) -> None:
    handle = reservation["session_handle"]
    host = reservation.get("execution_machine")
    fleet = _parse_fleet_body_handle(handle)
    if _parse_local_body_handle(handle):
        observed_host = supervisor.machine
    elif fleet is not None:
        observed_host = fleet[0]
    else:
        observed_host = (task["owner"] or "").partition("/")[0]
    if host and (not observed_host or normalize_host(observed_host) != normalize_host(host)):
        raise DispatchError(409, "liveness observation is not from the recorded execution host")
    supervisor.client.requeue_gone_owner(
        task["id"], supervisor=supervisor.supervisor_id, key=reservation["key"], status=task["status"],
        generation=task["generation"], owner=task["owner"], native=task["owner_session_id"],
        session_handle=handle, worktree=reservation["worktree"], startup_revision=reservation["startup_revision"],
        state=reservation["state"], updated_at=reservation["updated_at"], task_updated_at=task["updated_at"],
        wake_id=task.get("wake_operation_id"), wake_status=task.get("wake_status"), productive=productive,
    )
