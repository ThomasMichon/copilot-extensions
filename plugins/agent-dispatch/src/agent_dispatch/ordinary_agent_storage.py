"""Reservation-fenced ordinary agent facts; legacy receipts remain evidence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .ordinary_agent_facts import (
    identity, native_conflict, owner_record, validate_created, validate_resumed,
)
from .ordinary_native_bindings import (
    RESERVATION_NATIVE_IDENTITY_PROTOCOL,
    inherited_retirement, native_bindings, reservation_native_identity, retained_redrive_facts,
)
from .queue_records import SpawnReservation, SpawnState, Status, TaskError


def reservation_native_facts(queue: Any, key: str) -> dict:
    with queue._connect() as conn:
        conn.execute("BEGIN")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if row is None or task is None:
            raise TaskError("ordinary assignment has no current task/reservation")
        pool_bound = conn.execute(
            "SELECT 1 FROM pool_reservation_bindings WHERE reservation_key=?", (key,),
        ).fetchone() is not None
        from .queue_pool_placement import pool_assignment
        assigned = pool_assignment(conn, task.id) if pool_bound else None
        native, native_error = None, None
        try:
            native = reservation_native_identity(conn, row)
        except TaskError as exc:
            native_error = str(exc)
        try:
            known, retired = native_bindings(conn, row, task)
            reconcile_retirement = inherited_retirement(conn, row, task)
            error = _admission_error(conn, row, task, now=queue._now(None))
        except TaskError as exc:
            known, retired, reconcile_retirement, error = [], [], False, str(exc)
        try:
            cli_error = (
                _admission_error(conn, row, task, now=queue._now(None), driver="cli")
                if pool_bound and row["driver"] == "cli" else None
            )
        except TaskError as exc:
            cli_error = str(exc)
        redrive, redrive_error = None, None
        if row["driver"] == "ordinary-bridge":
            try:
                redrive = retained_redrive_facts(conn, row, task)
            except TaskError as exc:
                redrive_error = str(exc)
    return {
        "reservation_native_identity_protocol": RESERVATION_NATIVE_IDENTITY_PROTOCOL,
        "reservation_native_session_id": native,
        "reservation_native_identity_error": native_error,
        "reservation_pool_bound": pool_bound,
        "reservation_pool_assignment": asdict(assigned) if assigned is not None else None,
        "known_native_session_ids": known, "retired_native_session_ids": retired,
        "ordinary_assignment_error": error,
        "cli_initial_start_error": cli_error,
        "inherited_retirement_reconcilable": reconcile_retirement,
        "ordinary_redrive": redrive, "ordinary_redrive_error": redrive_error,
    }


def _admission_error(
    conn: Any, row: Any, task: Any, *, now: float, driver: str = "ordinary-bridge",
) -> str | None:
    from .queue_pool_placement import pool_assignment
    assigned = pool_assignment(conn, task.id)
    placed = assigned is not None
    if assigned is not None and (
        assigned.reservation_key != row["key"] or assigned.task_generation != task.generation
        or row["driver"] != driver or row["startup_revision"] != 1 or row["startup_fenced"]
    ):
        return "ordinary pool assignment incarnation or driver changed"
    if (
        row["state"] != SpawnState.RESERVING or row["release_requested"]
        or row["conversation_retired"] and not inherited_retirement(conn, row, task)
        or row["conclusion_state"] == "pending"
        or row["exclusive_key"] and row["exclusive_released"]
        or task.hold_reason is not None or task.awaiting_steer
        or task.status != (Status.CLAIMED if placed else Status.QUEUED) or task.owner is not None
        or task.source == "ci-repair" or row["exclusive_key"] != task.exclusive_key
    ):
        return "ordinary agent reservation admission fence changed"
    if placed:
        from .queue_pool_startup import require_pool_decision
        try:
            require_pool_decision(conn, task, now)
        except TaskError as exc:
            return str(exc)
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


def _bridge_created_host(
    row: Any, task: Any, actual: Any, agent_info: dict, *, resumed: bool,
    worktree_record: dict | None, startup_invocation: dict | None,
    execution_machine: str | None,
) -> str | None:
    """Return the creating host only when Bridge provably created this worktree."""
    if (
        resumed or worktree_record is not None or row["worktree_ownership"]
        or row["worktree"] or row["agent_info"] or task.target_worktree
        or not isinstance(startup_invocation, dict)
    ):
        return None
    binding = startup_invocation.get("binding") or {}
    creation = agent_info.get("creation")
    created = creation.get("worktree") if isinstance(creation, dict) else None
    if (
        binding.get("origin_kind") != "created" or not isinstance(created, dict)
        or creation.get("status") != "complete"
        or type(creation.get("replayed")) is not bool
        or created.get("id") != actual.worktree
        or not isinstance(created.get("creation_nonce"), str)
        or created["creation_nonce"] != binding.get("creation_nonce")
    ):
        return None
    host = execution_machine or created.get("execution_machine")
    return host.strip().casefold() if isinstance(host, str) and host.strip() else None


def _record_agent(
    queue: Any, key: str, *, task_generation: int, agent_info: dict,
    resumed: bool, worktree_record: dict | None, startup_invocation: dict | None = None,
    execution_machine: str | None = None,
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
        if error := _admission_error(conn, row, task, now=queue._now(None)):
            raise TaskError(error)
        from .queue_pool_placement import pool_assignment, require_pool_workspace
        from .bridge_remote import normalize_host
        if execution_machine is not None and (
            not isinstance(execution_machine, str) or not execution_machine.strip()
            or row["execution_machine"] is not None
            and normalize_host(row["execution_machine"]) != normalize_host(execution_machine)
        ):
            raise TaskError("ordinary startup execution machine cannot change")
        if pool_assignment(conn, task.id) is not None:
            if startup_invocation is None:
                raise TaskError("ordinary pool startup requires its immutable initial invocation")
            if execution_machine is None:
                raise TaskError("ordinary pool startup requires its retained execution machine")
            require_pool_workspace(
                conn, task, actual.worktree, execution_machine, driver=row["driver"],
            )
        else:
            require_pool_workspace(
                conn, task, actual.worktree, execution_machine or row["execution_machine"],
                driver=row["driver"], reservation_key=key,
            )
        if row["agent_info"]:
            from .ordinary_invocation import historical_intent, same_facts
            persisted = json.loads(row["agent_info"])
            if "startup_invocation" in persisted:
                frozen = historical_intent(persisted["startup_invocation"])
                if not same_facts(frozen, startup_invocation):
                    raise TaskError("ordinary startup invocation is already bound")
            elif startup_invocation is not None:
                raise TaskError("historical ordinary invocation is unavailable")
        from .ordinary_writer_admission import REQUIREMENT, retain_requirement
        retain_requirement(json.loads(row["agent_info"]) if row["agent_info"] else None, agent_info)
        expected_writer = json.loads(row[REQUIREMENT]) if row[REQUIREMENT] else None
        if expected_writer != agent_info.get(REQUIREMENT):
            raise TaskError("ordinary Bridge original writer requirement differs from allocation intent")
        if row["task_relationship_required"] and REQUIREMENT not in agent_info:
            raise TaskError("governed ordinary startup lacks its original writer requirement")
        if startup_invocation is not None:
            from .ordinary_invocation import same_facts, validate_storage
            validate_storage(startup_invocation, task, row, agent_info)
            if not same_facts(agent_info.get("startup_invocation", startup_invocation), startup_invocation):
                raise TaskError("ordinary invocation storage conflict")
        elif "startup_invocation" in agent_info:
            raise TaskError("keyed ordinary intent requires explicit storage capability")
        if REQUIREMENT in agent_info and (
            startup_invocation is None
            or startup_invocation["binding"].get("creation_nonce")
            != agent_info[REQUIREMENT]["creation_nonce"]
        ):
            raise TaskError("ordinary Bridge writer requirement changed its allocation nonce")
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
        created_host = _bridge_created_host(
            row, task, actual, agent_info, resumed=resumed, worktree_record=worktree_record,
            startup_invocation=startup_invocation, execution_machine=execution_machine,
        )
        conn.execute(
            "UPDATE spawn_reservations SET worktree=?,session_handle=?,agent_info=?,"
            "worktree_ownership=?,creating_host=?,driver='ordinary-bridge',allocation_project=?,"
            "worktree_path=?,execution_machine=?,conversation_retired=0,updated_at=? WHERE key=?",
            (
                actual.worktree, handle, encoded,
                "created" if created_host else row["worktree_ownership"] or "targeted",
                created_host or row["creating_host"],
                actual.project, actual.path, row["execution_machine"] or execution_machine, queue._now(None), key,
            ),
        )
        result = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(result)


def record_created_agent(
    queue: Any, key: str, *, task_generation: int, agent_info: dict,
    worktree_record: dict | None = None, startup_invocation: dict | None = None,
    execution_machine: str | None = None,
) -> SpawnReservation:
    validate_created(agent_info)
    return _record_agent(
        queue, key, task_generation=task_generation, agent_info=agent_info,
        resumed=False, worktree_record=worktree_record, startup_invocation=startup_invocation,
        execution_machine=execution_machine,
    )


def record_resumed_agent(
    queue: Any, key: str, *, task_generation: int, session_info: dict,
    worktree_record: dict, startup_invocation: dict | None = None,
    execution_machine: str | None = None,
) -> SpawnReservation:
    validate_resumed(session_info)
    return _record_agent(
        queue, key, task_generation=task_generation,
        agent_info={**session_info, "fact_kind": "resumed"},
        resumed=True, worktree_record=worktree_record, startup_invocation=startup_invocation,
        execution_machine=execution_machine,
    )
