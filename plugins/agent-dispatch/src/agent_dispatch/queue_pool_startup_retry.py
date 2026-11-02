"""Fence same-native retry admission and caller completion inside the spawn write."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from .ordinary_native_bindings import reservation_native_identity
from .pool_startup_retry_contract import retry_invocation
from .queue_pool_startup import STARTUP_ATTEMPTS, _current
from .queue_records import SpawnReservation, SpawnState, Status, TaskError


def prepare_mutation(
    owner: Any, conn: sqlite3.Connection, row: sqlite3.Row, binding: dict, *,
    completion: bool | None, ts: float,
) -> bool:
    """Validate and record a retry/completion; True is an unchanged exact replay."""
    expected_fields = {
        "version", "task_id", "reservation_key", "generation", "startup_revision",
        "native_id", "worktree", "machine", "reserved_by", "action",
    }
    action = binding.get("action")
    fields = expected_fields | ({"outcome_kind", "detail"} if action == "complete" else set())
    if (
        not math.isfinite(ts) or set(binding) != fields
        or type(binding.get("version")) is not int or binding["version"] != 1
        or action not in {"retry", "complete"}
        or any(not isinstance(binding.get(name), str) or not binding[name].strip() for name in (
            "task_id", "reservation_key", "native_id", "worktree", "machine", "reserved_by",
        ))
        or type(binding.get("generation")) is not int or binding["generation"] < 1
        or type(binding.get("startup_revision")) is not int or binding["startup_revision"] < 1
    ):
        raise TaskError("pool retry requires its exact caller, task and native binding")
    assignment = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE reservation_key=?", (row["key"],),
    ).fetchone()
    task = owner._fetch(conn, row["task_id"])
    if (
        assignment is None or task is None
        or assignment["task_id"] != binding["task_id"] or row["task_id"] != binding["task_id"]
        or row["key"] != binding["reservation_key"]
        or assignment["task_generation"] != binding["generation"]
        or row["startup_revision"] != binding["startup_revision"]
        or row["driver"] != "cli" or row["conversation_retired"]
        or row["worktree"] != binding["worktree"]
        or row["execution_machine"] != binding["machine"]
        or row["reserved_by"] != binding["reserved_by"]
        or reservation_native_identity(conn, row) != binding["native_id"]
        or row["cooling_token"] is not None
    ):
        raise TaskError("pool retry incarnation, caller or retained native changed")
    base = {name: binding[name] for name in expected_fields - {"action"}}
    if action == "retry":
        owner.require_worker_admission()
        task, assignment, row = _current(
            owner, conn, row["key"], binding["generation"], binding["startup_revision"], ts,
            binding["task_id"],
        )
        if (
            completion is not False or not row["startup_complete"]
            or assignment["reachable_at"] is not None
            or assignment["outcome_revision"] != row["startup_revision"]
            or assignment["outcome_kind"] not in {"startup_failed", "startup_rejected"}
            or ts >= assignment["startup_deadline"]
            or assignment["startup_attempts"] - assignment["episode_start_attempt"] >= STARTUP_ATTEMPTS
            or row["conclusion_state"] == "pending"
        ):
            raise TaskError("pool retry requires a completed original failed startup within its budget")
        from .queue_pool_placement import require_pool_start
        require_pool_start(
            conn, task, f"{assignment['machine']}/{row['worktree']}",
            binding["native_id"], binding["generation"],
        )
        invocation = retry_invocation(base, row["startup_revision"] + 1)
        conn.execute(
            "UPDATE task_pool_assignments SET retry_invocation=? WHERE reservation_key=?",
            (json.dumps(invocation, sort_keys=True), row["key"]),
        )
        note = "pool startup retry admitted: " + json.dumps(invocation, sort_keys=True)
    else:
        outcome, detail = binding["outcome_kind"], binding["detail"]
        if outcome not in {"startup_succeeded", "startup_failed", "startup_rejected", "startup_unknown"} or (
            not isinstance(detail, str) or not detail.strip()
        ) or completion is not (None if outcome == "startup_unknown" else True):
            raise TaskError("pool retry completion requires the original classified caller return")
        retained_invocation = json.loads(assignment["retry_invocation"]) if assignment["retry_invocation"] else None
        if retained_invocation != retry_invocation(base, row["startup_revision"]):
            raise TaskError("pool retry completion does not own this invocation")
        current = task.generation == binding["generation"] and (
            task.status == Status.CLAIMED and task.owner is None
            or task.status == Status.STARTED and task.owner_session_id == binding["native_id"]
            and task.owner == f"{assignment['machine']}/{row['worktree']}"
        )
        late = (
            task.generation == binding["generation"] + 1 and task.status == Status.QUEUED
            and assignment["exhausted_at"] is not None and row["state"] == SpawnState.RELEASING
            and row["release_requested"]
        )
        if not (current or late) or row["state"] not in {SpawnState.SPAWNED, SpawnState.RELEASING}:
            raise TaskError("pool retry completion task incarnation changed")
        stored_kind = None if outcome == "startup_succeeded" else outcome
        if row["startup_complete"]:
            if assignment["outcome_kind"] == stored_kind and (
                stored_kind is None or assignment["outcome_revision"] == row["startup_revision"]
                and assignment["outcome_detail"] == detail
            ):
                return True
            raise TaskError("completed pool retry outcome cannot be replaced")
        if row["startup_fenced"]:
            raise TaskError("pool retry completion startup is fenced")
        conn.execute(
            "UPDATE task_pool_assignments SET outcome_kind=?, outcome_detail=?, outcome_revision=?,"
            " outcome_at=? WHERE reservation_key=?",
            (stored_kind, detail if stored_kind else None, row["startup_revision"] if stored_kind else None,
             ts if stored_kind else None, row["key"]),
        )
        note = f"pool startup retry completion: {outcome}: {detail}"
    owner._audit(
        conn, task.id, ts=ts, from_status=task.status, to_status=task.status,
        worker=row["reserved_by"], note=note,
    )
    return False


def record_retry(owner: Any, key: str, binding: dict) -> SpawnReservation:
    """Use the existing spawn publication owner, with no external effect in its lock."""
    action = binding["action"]
    completion = False if action == "retry" else (
        None if binding["outcome_kind"] == "startup_unknown" else True
    )
    result: SpawnReservation = owner.record_spawn(
        key, session_handle=binding["native_id"], worktree=binding["worktree"],
        startup_revision=binding["startup_revision"], startup_complete=completion,
        pool_startup_retry=binding,
    )
    return result


def refuse_legacy_retry(conn: sqlite3.Connection, key: str) -> None:
    """An acquired exact retry cannot be completed or advanced by a legacy caller."""
    pending = conn.execute(
        "SELECT retry_invocation FROM task_pool_assignments WHERE reservation_key=?", (key,),
    ).fetchone()
    if pending is not None and pending["retry_invocation"]:
        raise TaskError("pool retry completion requires its negotiated exact operation")
