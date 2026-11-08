"""Observe outside SQLite; atomically fence/requeue only the unchanged declined claim."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
import sqlite3
from typing import Any

from .queue_pool_decline import PoolDeclineCloseout, _QueueOwner, current_pool_decline
from .queue_records import SpawnState, Status, TaskError


def _state(owner: _QueueOwner, conn: sqlite3.Connection, task_id: str, binding: dict) -> dict:
    task = owner._fetch(conn, task_id)
    decline = current_pool_decline(conn, task_id)
    row = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (binding["reservation_key"],),
    ).fetchone()
    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id=?", (task_id,),
    ).fetchone()
    if (
        task is None or decline is None or row is None or assignment is None
        or any(getattr(decline, key) != value or type(getattr(decline, key)) is not type(value)
               for key, value in binding.items())
        or task.status != Status.CLAIMED or task.generation != binding["generation"]
        or task.owner is not None or task.hold_reason is not None or task.awaiting_steer
        or row["startup_revision"] != binding["startup_revision"] or not row["startup_complete"]
        or assignment["assignment_released"]
    ):
        raise TaskError("worker decline closeout incarnation changed or is held")
    from .queue_execution_handoff import require_execution_admission
    from .queue_pool_placement import require_pool_start
    require_execution_admission(conn, task_id)
    require_pool_start(
        conn, task, binding["worker_id"], binding["native_id"], binding["generation"], declining=True,
    )
    return {
        "task": dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()),
        "reservation": dict(row), "assignment": dict(assignment), "decline": asdict(decline),
    }


def _replay(conn: sqlite3.Connection, task_id: str, binding: dict) -> dict | None:
    row = conn.execute(
        "SELECT receipt FROM pool_worker_decline_closeouts WHERE reservation_key=?",
        (binding["reservation_key"],),
    ).fetchone()
    if row is None:
        return None
    receipt = json.loads(row["receipt"])
    if receipt["task_id"] != task_id or receipt["binding"] != binding:
        raise TaskError("worker decline closeout cannot replace its original receipt")
    return receipt


def close(
    owner: _QueueOwner, task_id: str, *, now: float | None = None, **binding: Any,
) -> PoolDeclineCloseout:
    """No caller observation parameter exists: this owner acquires its own evidence."""
    if (
        any(not isinstance(binding[name], str) or not binding[name].strip()
            for name in ("worker_id", "native_id", "reservation_key"))
        or any(type(binding[name]) is not int or binding[name] < 1
               for name in ("generation", "startup_revision"))
    ):
        raise TaskError("worker decline closeout requires its exact incarnation")
    with owner._connect() as conn:
        conn.execute("BEGIN")
        replay = _replay(conn, task_id, binding)
        if replay is not None:
            return PoolDeclineCloseout(replay, False)
        original = _state(owner, conn, task_id, binding)
    from .pool_decline_observation import observe
    observation = observe(original["task"], original["reservation"], original["decline"])
    with owner._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay(conn, task_id, binding)
        if replay is not None:
            return PoolDeclineCloseout(replay, False)
        current = _state(owner, conn, task_id, binding)
        if current != original:
            raise TaskError("worker decline changed during owner observation; custody retained")
        ts = owner._now(now)
        if not math.isfinite(ts):
            raise TaskError("worker decline requires finite time")
        from .queue_pool_startup import COOLDOWN_SECONDS
        assignment, task, reservation = current["assignment"], current["task"], current["reservation"]
        stage = min(assignment["cooldown_stage"] + 1, len(COOLDOWN_SECONDS))
        due = max(task["not_before"], ts + COOLDOWN_SECONDS[stage - 1])
        exclusions = sorted(set(json.loads(task["excludes"])) | set(current["decline"]["exclusions"]))
        receipt = {
            "task_id": task_id, "binding": binding, "queued_generation": binding["generation"] + 1,
            "exclusions": exclusions, "not_before": due, "closed_at": ts, "observation": observation,
            "process_retirement": False,
        }
        conn.execute(
            "UPDATE tasks SET status=?, generation=generation+1, excludes=?, not_before=?,"
            " updated_at=?, claimed_at=NULL, lease_expires_at=NULL, activity=NULL,"
            " activity_updated_at=? WHERE id=?",
            (Status.QUEUED, json.dumps(exclusions), due, ts, ts, task_id),
        )
        conn.execute(
            "UPDATE task_pool_assignments SET assignment_released=1, cooldown_stage=?,"
            " exhausted_at=?, exhaustion_reason='worker_declined' WHERE task_id=?",
            (stage, ts, task_id),
        )
        conn.execute(
            "UPDATE spawn_reservations SET state=?, release_requested=1, startup_fenced=1,"
            " release_disposition='settled', conclusion_state=COALESCE(conclusion_state,'pending'),"
            " detail=?, updated_at=? WHERE key=?",
            (SpawnState.RELEASING, "worker declined after observed own-workspace closeout",
             ts, reservation["key"]),
        )
        conn.execute(
            "INSERT INTO pool_worker_decline_closeouts VALUES (?, ?)",
            (reservation["key"], json.dumps(receipt, sort_keys=True)),
        )
        owner._audit(
            conn, task_id, ts=ts, from_status=Status.CLAIMED, to_status=Status.QUEUED,
            worker=binding["worker_id"],
            note="worker decline closeout observed; routing applied; native retirement pending",
            unassigned_worker=binding["worker_id"],
        )
        conn.commit()
        return PoolDeclineCloseout(receipt, True)
