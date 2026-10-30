"""Reservation-fenced ordinary agent facts; legacy receipts remain evidence."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import (
    identity, native_conflict, owner_record, validate_created, validate_resumed,
)
from .ordinary_native_bindings import inherited_retirement, native_bindings, retained_redrive_facts
from .queue_records import SpawnReservation, SpawnState, Status, TaskError


def reservation_native_facts(queue: Any, key: str) -> dict:
    with queue._connect() as conn:
        conn.execute("BEGIN")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if row is None or task is None:
            raise TaskError("ordinary assignment has no current task/reservation")
        try:
            known, retired = native_bindings(conn, row, task)
            reconcile_retirement = inherited_retirement(conn, row, task)
            error = _admission_error(conn, row, task)
        except TaskError as exc:
            known, retired, reconcile_retirement, error = [], [], False, str(exc)
        redrive, redrive_error = None, None
        if row["driver"] == "ordinary-bridge":
            try:
                redrive = retained_redrive_facts(conn, row, task)
            except TaskError as exc:
                redrive_error = str(exc)
    return {
        "known_native_session_ids": known, "retired_native_session_ids": retired,
        "ordinary_assignment_error": error,
        "inherited_retirement_reconcilable": reconcile_retirement,
        "ordinary_redrive": redrive, "ordinary_redrive_error": redrive_error,
    }


def _admission_error(conn: Any, row: Any, task: Any) -> str | None:
    if (
        row["state"] != SpawnState.RESERVING or row["release_requested"]
        or row["conversation_retired"] and not inherited_retirement(conn, row, task)
        or row["conclusion_state"] == "pending"
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
    resumed: bool, worktree_record: dict | None, startup_invocation: dict | None = None,
) -> SpawnReservation:
    actual = identity(agent_info)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if row is None or task is None or task.generation != task_generation:
            raise TaskError("ordinary agent reservation admission fence changed")
        from .queue_spawn_cooling import require_no_cooling
        require_no_cooling(row)
        if error := _admission_error(conn, row, task):
            raise TaskError(error)
        if row["agent_info"]:
            from .ordinary_invocation import historical_intent, same_facts
            persisted = json.loads(row["agent_info"])
            if "startup_invocation" in persisted:
                frozen = historical_intent(persisted["startup_invocation"])
                if not same_facts(frozen, startup_invocation):
                    raise TaskError("ordinary startup invocation is already bound")
            elif startup_invocation is not None:
                raise TaskError("historical ordinary invocation is unavailable")
        if startup_invocation is not None:
            from .ordinary_invocation import same_facts, validate_storage
            validate_storage(startup_invocation, task, row, agent_info)
            if not same_facts(agent_info.get("startup_invocation", startup_invocation), startup_invocation):
                raise TaskError("ordinary invocation storage conflict")
        elif "startup_invocation" in agent_info:
            raise TaskError("keyed ordinary intent requires explicit storage capability")
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
            if (startup_invocation is not None and startup_invocation["binding"]["creation_nonce"]
                    != record["creation_nonce"]):
                raise TaskError("ordinary invocation incarnation changed")
            if (
                record["id"] != actual.worktree or record["repo"] != actual.project
                or record["path"] != actual.path
            ):
                raise TaskError("ordinary agent does not match the exact ground workspace")
            if record.get("last_session_id") != actual.native:
                # A real keyed create can complete the positively sessionless allocation.
                from .ordinary_agent_facts import sessionless
                if resumed or not sessionless(record):
                    raise TaskError("ordinary agent does not match the ground native head")
        elif resumed or row["worktree"] and not row["agent_info"]:
            raise TaskError("assigned agent requires the exact ground owner record")
        if row["conversation_retired"] and not resumed:
            raise TaskError("retired history requires a proven current native head")
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
        if startup_invocation is not None:
            recorded["startup_invocation"] = startup_invocation
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
            "worktree_path=?,conversation_retired=0,updated_at=? WHERE key=?",
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
    worktree_record: dict | None = None, startup_invocation: dict | None = None,
) -> SpawnReservation:
    validate_created(agent_info)
    return _record_agent(
        queue, key, task_generation=task_generation, agent_info=agent_info,
        resumed=False, worktree_record=worktree_record, startup_invocation=startup_invocation,
    )


def record_resumed_agent(
    queue: Any, key: str, *, task_generation: int, session_info: dict,
    worktree_record: dict, startup_invocation: dict | None = None,
) -> SpawnReservation:
    validate_resumed(session_info)
    return _record_agent(
        queue, key, task_generation=task_generation,
        agent_info={**session_info, "fact_kind": "resumed"},
        resumed=True, worktree_record=worktree_record, startup_invocation=startup_invocation,
    )
