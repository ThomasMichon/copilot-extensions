"""Reservation-fenced ordinary agent facts; legacy receipts remain evidence."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import (
    identity, native_conflict, owner_record, validate_created, validate_resumed,
)
from .queue_records import SpawnReservation, SpawnState, Status, TaskError


def native_bindings(conn: Any, row: Any, task: Any) -> tuple[list[str], list[str]]:
    """Read native bindings only for this retained allocation and exact workspace."""
    known: set[str] = set()
    retired: set[str] = set()
    if task.owner_session_id:
        known.add(task.owner_session_id)
    if row["session_handle"] and not row["session_handle"].startswith("local-body:"):
        if row["driver"] == "cli":
            known.add(row["session_handle"])
    if row["agent_info"]:
        known.add(identity(json.loads(row["agent_info"])).native)
    if row["worktree"]:
        histories = conn.execute(
            "SELECT s.agent_info,s.conversation_retired,s.release_requested,s.state,"
            "s.session_handle,a.native_session_id "
            "FROM spawn_reservations s LEFT JOIN acquisition_receipts a "
            "ON a.reservation_key=s.key WHERE s.worktree=? "
            "AND (s.task_id=? OR s.creation_key=?)",
            (row["worktree"], task.id, row["creation_key"]),
        ).fetchall()
        for history in histories:
            native = history["native_session_id"]
            if not native and history["agent_info"]:
                native = identity(json.loads(history["agent_info"])).native
            if native:
                known.add(native)
                if history["conversation_retired"] or (
                    history["session_handle"] and history["release_requested"]
                    and history["state"] in {SpawnState.FAILED, SpawnState.SETTLED, SpawnState.REARMED}
                ):
                    retired.add(native)
    return sorted(known), sorted(retired)


def reservation_native_facts(queue: Any, key: str) -> dict:
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if row is None or task is None:
            raise TaskError("ordinary assignment has no current task/reservation")
        known, retired = native_bindings(conn, row, task)
        error = _admission_error(conn, row, task)
    return {
        "known_native_session_ids": known, "retired_native_session_ids": retired,
        "ordinary_assignment_error": error,
    }


def _admission_error(conn: Any, row: Any, task: Any) -> str | None:
    if (
        row["state"] != SpawnState.RESERVING or row["release_requested"]
        or row["conversation_retired"] or row["conclusion_state"] == "pending"
        or row["exclusive_key"] and row["exclusive_released"]
        or task.hold_reason is not None or task.status != Status.QUEUED or task.owner is not None
        or task.source == "ci-repair" or row["exclusive_key"] != task.exclusive_key
    ):
        return "ordinary agent reservation admission fence changed"
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND attempt>? LIMIT 1",
        (task.id, row["attempt"]),
    ).fetchone():
        return "ordinary agent reservation is no longer the current attempt"
    if row["worktree"] and conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE key<>? AND worktree=? "
        "AND (state IN ('reserving','spawned','cold','releasing') OR conclusion_state='pending')",
        (row["key"], row["worktree"]),
    ).fetchone():
        return "ordinary agent worktree has another active owner or cleanup"
    if row["exclusive_key"] and conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE key<>? AND exclusive_key=? "
        "AND exclusive_released=0 AND state IN ('reserving','spawned','cold','releasing')",
        (row["key"], row["exclusive_key"]),
    ).fetchone():
        return "ordinary agent exclusivity has another active owner"
    return None


def _record_agent(
    queue: Any, key: str, *, task_generation: int, agent_info: dict,
    resumed: bool, worktree_record: dict | None,
) -> SpawnReservation:
    actual = identity(agent_info)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if row is None or task is None or task.generation != task_generation:
            raise TaskError("ordinary agent reservation admission fence changed")
        if error := _admission_error(conn, row, task):
            raise TaskError(error)
        if (
            row["allocation_project"] and row["allocation_project"] != actual.project
            or row["worktree_path"] and row["worktree_path"] != actual.path
            or row["creation_key"] and (not isinstance(actual.path, str) or not actual.path)
            or row["worktree"] and row["worktree"] != actual.worktree
            or task.target_worktree and task.target_worktree != actual.worktree
        ):
            raise TaskError("ordinary agent does not match retained allocation provenance")
        if worktree_record is not None:
            record = owner_record(worktree_record, dict(row), task.id)
            if record.get("last_session_id") != actual.native:
                # A real keyed create can complete the positively sessionless allocation.
                from .ordinary_agent_facts import sessionless
                if resumed or not sessionless(record):
                    raise TaskError("ordinary agent does not match the ground native head")
        elif resumed:
            raise TaskError("resumed agent requires the exact ground owner record")
        known, retired = native_bindings(conn, row, task)
        if native_conflict(actual.native, known, retired):
            raise TaskError("ordinary agent conflicts with known or retired native identity")
        if not known and worktree_record is None and conn.execute(
            "SELECT 1 FROM acquisition_markers WHERE reservation_key=?", (key,),
        ).fetchone():
            raise TaskError("legacy acquisition has no proven current native/history facts")
        handle = f"local-body:{actual.bridge}"
        if row["agent_info"]:
            previous = identity(json.loads(row["agent_info"]))
            if previous != actual:
                raise TaskError("ordinary agent identity is already bound")
        elif row["session_handle"] and row["session_handle"] != handle:
            if not resumed:
                raise TaskError("ordinary agent does not match retained reservation identity")
        if conn.execute(
            "SELECT 1 FROM spawn_reservations WHERE key<>? AND worktree=? "
            "AND (state IN ('reserving','spawned','cold','releasing') OR conclusion_state='pending')",
            (key, actual.worktree),
        ).fetchone():
            raise TaskError("ordinary agent worktree has another active owner or cleanup")
        if row["exclusive_key"] and conn.execute(
            "SELECT 1 FROM spawn_reservations WHERE key<>? AND exclusive_key=? "
            "AND exclusive_released=0 AND state IN ('reserving','spawned','cold','releasing')",
            (key, row["exclusive_key"]),
        ).fetchone():
            raise TaskError("ordinary agent exclusivity has another active owner")
        recorded = {**agent_info, "task_generation": task_generation}
        if resumed and row["agent_info"]:
            previous_info = json.loads(row["agent_info"])
            if "last_send" in previous_info:
                recorded.setdefault("last_send", previous_info["last_send"])
        encoded = json.dumps(recorded, sort_keys=True)
        if row["agent_info"] and not resumed and row["agent_info"] != encoded:
            raise TaskError("created-agent identity is already bound")
        conn.execute(
            "UPDATE spawn_reservations SET worktree=?,session_handle=?,agent_info=?,"
            "worktree_ownership=?,driver='ordinary-bridge',allocation_project=?,"
            "worktree_path=?,updated_at=? WHERE key=?",
            (
                actual.worktree, handle, encoded,
                row["worktree_ownership"] or "targeted",
                actual.project, actual.path, queue._now(None), key,
            ),
        )
        result = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(result)


def record_created_agent(
    queue: Any, key: str, *, task_generation: int, agent_info: dict,
    worktree_record: dict | None = None,
) -> SpawnReservation:
    validate_created(agent_info)
    return _record_agent(
        queue, key, task_generation=task_generation, agent_info=agent_info,
        resumed=False, worktree_record=worktree_record,
    )


def record_resumed_agent(
    queue: Any, key: str, *, task_generation: int, session_info: dict,
    worktree_record: dict,
) -> SpawnReservation:
    validate_resumed(session_info)
    return _record_agent(
        queue, key, task_generation=task_generation,
        agent_info={**session_info, "fact_kind": "resumed"},
        resumed=True, worktree_record=worktree_record,
    )
