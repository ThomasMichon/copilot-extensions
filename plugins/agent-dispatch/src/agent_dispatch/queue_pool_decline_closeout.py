"""Observe outside SQLite; atomically fence/requeue only the unchanged declined claim."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json
import math
import sqlite3
import threading
from typing import Any
import weakref

from .queue_pool_decline import PoolDeclineCloseout, _QueueOwner, current_pool_decline
from .queue_records import SpawnState, Status, TaskError
from .task_obligations import _worktree_requirement


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
    row_task = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    relationship = _relationship(conn, task_id, dict(row))
    if relationship is not None and (
        row_task["target_worktree"] is not None or _worktree_requirement(row_task["requires"])
    ):
        # A requeue would keep the pin, so the retained claim could never settle.
        raise TaskError("worker decline cannot settle a task claim pinned to a worktree; custody retained")
    return {
        "task": row_task,
        "reservation": dict(row), "assignment": dict(assignment), "decline": asdict(decline),
        "relationship": relationship,
    }


def _relationship(conn: sqlite3.Connection, task_id: str, reservation: dict) -> dict | None:
    """Select this allocation's retained task claim; unresolved claims retain custody."""
    from .queue_task_relationships import latest_relationship
    row = latest_relationship(conn, task_id)
    value = json.loads(row["task_relationship"]) if row is not None else None
    if value is not None and value["request"].get("reservation_key") == reservation["key"]:
        if value["state"] != "bound":
            raise TaskError("worker decline task relationship is unresolved; custody retained")
        return value
    if (value is not None and value["state"] not in {"closed", "absent"}) or reservation.get(
        "task_relationship_required"
    ):
        raise TaskError("worker decline lacks its original task relationship; custody retained")
    return None


_settle_locks: weakref.WeakValueDictionary[tuple[str, str], threading.Lock] = (
    weakref.WeakValueDictionary()
)
_settle_locks_guard = threading.Lock()


def _settle_lock(task_id: str, event_id: str) -> threading.Lock:
    with _settle_locks_guard:
        lock = _settle_locks.get((task_id, event_id))
        if lock is None:
            lock = threading.Lock()
            _settle_locks[(task_id, event_id)] = lock
        return lock


def _closed(owner: _QueueOwner, task_id: str, event_id: str) -> bool:
    current = owner.task_relationship(task_id, event_id=event_id)
    return current is not None and current["state"] == "closed"


def _settle(
    owner: _QueueOwner, task_id: str, receipt: dict, *,
    authority_guard: Callable[[], None] | None = None,
) -> dict | None:
    """Settle only the exact claim this confirmed disposition retired, through its owner.

    The supervisor's closeout and the worker's settlement request race for the same
    claim; serialize them so the later caller observes the earlier close instead of
    failing a duplicate reconcile.
    """
    event_id = receipt.get("task_relationship_event_id")
    if event_id is None:
        return None
    closed = {"event_id": event_id, "state": "closed"}
    lock = _settle_lock(task_id, event_id)
    with lock:
        if authority_guard is not None:
            authority_guard()
        if _closed(owner, task_id, event_id):
            return closed
        try:
            if authority_guard is None:
                settled = owner.reconcile_task_relationship(task_id, event_id=event_id)
            else:
                settled = owner.reconcile_task_relationship(
                    task_id, event_id=event_id, authority_guard=authority_guard,
                )
        except Exception as exc:  # any failure retains the claim visibly
            if authority_guard is not None:
                authority_guard()
            try:
                if _closed(owner, task_id, event_id):
                    return closed
            except Exception:
                pass
            error = str(exc)
            if exc.__cause__ is not None:
                error = f"{error}: {exc.__cause__}"
            return {"event_id": event_id, "state": "retained", "error": error[:1024]}
    return {"event_id": event_id, "state": settled["state"]}


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


def settle(owner: _QueueOwner, task_id: str, **binding: Any) -> PoolDeclineCloseout:
    """Converge a lost or failed settlement; never requeue or settle without a receipt."""
    with owner._connect() as conn:
        replay = _replay(conn, task_id, binding)
    if replay is None:
        raise TaskError("worker decline disposition is unconfirmed; custody retained")
    return PoolDeclineCloseout(replay, False, _settle(owner, task_id, replay))


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
        original = _state(owner, conn, task_id, binding) if replay is None else None
    if replay is not None:
        return PoolDeclineCloseout(replay, False, _settle(owner, task_id, replay))
    from .pool_decline_observation import observe
    relationship = original["relationship"]
    observation = observe(
        original["task"], original["reservation"], original["decline"], relationship,
    )
    with owner._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay(conn, task_id, binding)
        if replay is not None:
            conn.rollback()
            return PoolDeclineCloseout(replay, False, _settle(owner, task_id, replay))
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
        if relationship is not None:
            receipt["task_relationship_event_id"] = relationship["request"]["event_id"]
        conn.execute(
            "UPDATE tasks SET status=?, generation=generation+1, excludes=?, not_before=?,"
            " owner_session_id=NULL,"
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
            (SpawnState.RELEASING, "worker declined after observed own-workspace closeout"
             if relationship is None else "worker declined after observed own-workspace readiness",
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
    # Only this committed disposition permits the exact claim to settle.
    return PoolDeclineCloseout(receipt, True, _settle(owner, task_id, receipt))
