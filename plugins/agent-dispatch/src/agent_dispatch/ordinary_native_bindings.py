"""Native identity and retirement evidence for one retained allocation."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import identity
from .queue_records import SpawnState, Status, TaskError


def is_native_retired(row: Any) -> bool:
    """Use the reservation owner's marker or completed exact-absence release."""
    return bool(row["conversation_retired"]) or bool(
        row["release_requested"]
        and row["state"] in {SpawnState.FAILED, SpawnState.SETTLED, SpawnState.REARMED}
    )


def reservation_native_identity(conn: Any, history: Any) -> str | None:
    """Read one driver's retained native identity, including acquisition evidence."""
    handle = history["session_handle"]
    if "native_session_id" in history.keys():
        native = history["native_session_id"]
    else:
        receipt = conn.execute(
            "SELECT native_session_id FROM acquisition_receipts WHERE reservation_key=?",
            (history["key"],),
        ).fetchone()
        native = receipt["native_session_id"] if receipt is not None else None
    if history["agent_info"]:
        info = json.loads(history["agent_info"])
        if not isinstance(info, dict):
            raise TaskError("retained native identity is not an object")
        if history["driver"] == "restricted-bridge":
            from .restricted_delivery import validate_receipt
            validate_receipt(info)
            actual = info.get("native_session_id")
            if handle and handle != f"local-body:{info.get('session_id')}":
                raise TaskError("retained restricted bridge handle conflicts")
        else:
            actual = identity(info).native
        if native and actual != native:
            raise TaskError("retained native identity evidence conflicts")
        native = actual
    if history["driver"] == "cli" and handle and not handle.startswith("local-body:"):
        if native and native != handle:
            raise TaskError("retained CLI native identity evidence conflicts")
        native = handle
    return native


def _validate_origin(conn: Any, row: Any, origin: str | None) -> None:
    source = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key=?", (origin,),
    ).fetchone()
    if (
        source is None or source["allocation_reservation_key"] not in (None, source["key"])
        or source["worktree_ownership"] == "reused" and source["inherited_worktree"]
        or source["worktree"] not in (None, row["worktree"])
    ):
        raise TaskError("inherited workspace native origin is unproven")
    if source["worktree"] is not None:
        return
    # A lost create response can leave the root unbound after a successful keyed replay.
    bindings = conn.execute(
        "SELECT * FROM spawn_reservations WHERE allocation_reservation_key=? "
        "AND creation_key=? AND worktree=? AND agent_info IS NOT NULL",
        (origin, source["creation_key"], row["worktree"]),
    ).fetchall()
    if not source["creation_key"] or not bindings:
        raise TaskError("inherited workspace native origin is unproven")
    for binding in bindings:
        actual = identity(json.loads(binding["agent_info"]))
        if (
            actual.worktree != row["worktree"] or actual.project != binding["allocation_project"]
            or actual.path != binding["worktree_path"]
            or row["allocation_project"] and actual.project != row["allocation_project"]
            or row["worktree_path"] and actual.path != row["worktree_path"]
        ):
            raise TaskError("inherited workspace replay binding conflicts with its allocation")


def native_bindings(conn: Any, row: Any, task: Any) -> tuple[list[str], list[str]]:
    """Classify handles using their producing driver, not the retry's driver."""
    origin = row["allocation_reservation_key"]
    if row["worktree_ownership"] == "reused" and row["inherited_worktree"]:
        _validate_origin(conn, row, origin)
    known = {task.owner_session_id} if task.owner_session_id else set()
    retired: set[str] = set()
    handle_natives: dict[str, str] = {}
    histories = conn.execute(
        "SELECT s.*,a.native_session_id FROM spawn_reservations s "
        "LEFT JOIN acquisition_receipts a ON a.reservation_key=s.key "
        "WHERE (s.worktree=? OR s.worktree IS NULL AND (s.key=? OR s.allocation_reservation_key=?)) "
        "AND (s.task_id=? OR s.creation_key=? "
        "OR s.key=? OR s.allocation_reservation_key=? OR ("
        "s.exclusive_key=? AND s.session_handle IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM spawn_reservations carried WHERE carried.worktree=s.worktree "
        "AND (carried.task_id=? OR carried.creation_key=?) "
        "AND carried.session_handle=s.session_handle)))",
        (row["worktree"], origin, origin, task.id, row["creation_key"], origin, origin, row["exclusive_key"],
         task.id, row["creation_key"]),
    ).fetchall() if row["worktree"] else [row]
    evidence: list[tuple[Any, str | None]] = []
    for history in histories:
        handle = history["session_handle"]
        native = reservation_native_identity(conn, history)
        if native:
            if handle:
                if handle in handle_natives and handle_natives[handle] != native:
                    raise TaskError("retained session handle has conflicting native identities")
                handle_natives[handle] = native
        evidence.append((history, native))
    for history, native in evidence:
        handle = history["session_handle"]
        native = native or handle_natives.get(handle)
        is_retired = is_native_retired(history)
        if native:
            known.add(native)
            if is_retired:
                retired.add(native)
        elif is_retired and (handle or history["release_requested"]):
            raise TaskError("retired conversation has no proven native identity")
        elif handle and not handle.startswith("local-body:"):
            raise TaskError("retained bare session handle has no proven native identity")
    return sorted(known), sorted(retired)


def inherited_retirement(conn: Any, row: Any, task: Any) -> bool:
    """Permit inspection of a derived marker, never revival of its old native."""
    if not row["conversation_retired"] or row["agent_info"] or row["session_handle"]:
        return False
    _, retired = native_bindings(conn, row, task)
    return bool(retired)


def retained_start_generation(conn: Any, task: Any, info: dict) -> bool:
    """Later normal claims require the original native's real attachment history."""
    generation = info.get("task_generation")
    if type(generation) is not int or task.generation < generation:
        return False
    if task.generation <= generation + 1:
        return True
    previous = conn.execute(
        "SELECT session_id,worktree_id,machine FROM task_attachments "
        "WHERE task_id=? ORDER BY id DESC LIMIT 1", (task.id,),
    ).fetchone()
    actual = identity(info)
    return bool(
        previous is not None
        and previous["session_id"] == actual.native
        and previous["worktree_id"] == actual.worktree
        and previous["machine"] is not None
        and task.owner == f"{previous['machine']}/{actual.worktree}"
    )


def retained_redrive_facts(conn: Any, row: Any, task: Any) -> dict:
    """Describe a same-native queued continuation; never authorize replacement."""
    from .bridge_remote import normalize_host
    from .ordinary_agent_facts import native_conflict
    from .queue_execution_custody import (
        execution_scope_qualified, require_execution_send_reconciliation,
    )
    from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER

    if (
        row["driver"] != "ordinary-bridge"
        or row["state"] != SpawnState.SPAWNED
        or row["startup_fenced"] or row["release_requested"]
        or row["conversation_retired"] or row["cooling_token"] is not None
        or row["conclusion_state"] == "pending"
        or row["exclusive_key"] and row["exclusive_released"]
        or task.status != Status.QUEUED or task.owner is not None
        or task.owner_session_id is not None or task.hold_reason is not None
        or task.awaiting_steer or task.exclusive_key != row["exclusive_key"]
        or task.target_worktree is not None and task.target_worktree != row["worktree"]
        or not row["agent_info"]
    ):
        raise TaskError("retained native redrive task/reservation fence changed")
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND attempt>? LIMIT 1",
        (task.id, row["attempt"]),
    ).fetchone():
        raise TaskError("retained native redrive is not the current attempt")
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE key<>? "
        "AND (worktree=? OR exclusive_key IS NOT NULL AND exclusive_key=? AND exclusive_released=0) "
        "AND (state IN ('reserving','spawned','cold','releasing') OR conclusion_state='pending')",
        (row["key"], row["worktree"], row["exclusive_key"]),
    ).fetchone():
        raise TaskError("retained native redrive has conflicting execution custody")
    if execution_scope_qualified(conn, task.repo, task.source):
        raise TaskError("retained native redrive requires enrolled supervisor send admission")
    require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
    if conn.execute(
        "SELECT 1 FROM run_waiters WHERE task_id=? AND state IN ('active','preparing')",
        (task.id,),
    ).fetchone():
        raise TaskError("retained native redrive has an active detached waiter")
    for table in ("wake_outbox", "run_waiter_wakes"):
        wakes = conn.execute(f"SELECT * FROM {table} WHERE task_id=?", (task.id,)).fetchall()
        if any(
            not (table == "run_waiter_wakes" and wake["sender"] == CLAIM_RELEASE_SENDER)
            and (
                wake["execution_send_state"] in {"pending", "unknown"}
                or wake["status"] in {"pending", "delivering", "failed"}
                or wake["attempts"] > 0 and wake["status"] != "delivered"
            )
            for wake in wakes
        ):
            raise TaskError("retained native redrive has unresolved wake custody")
    actual = identity(json.loads(row["agent_info"]))
    previous = conn.execute(
        "SELECT session_id,worktree_id,machine FROM task_attachments "
        "WHERE task_id=? ORDER BY id DESC LIMIT 1", (task.id,),
    ).fetchone()
    if (
        previous is None or previous["session_id"] != actual.native
        or previous["worktree_id"] != actual.worktree or not previous["machine"]
        or actual.worktree != row["worktree"]
        or actual.project != row["allocation_project"] or actual.path != row["worktree_path"]
    ):
        raise TaskError("retained native redrive has no matching original attachment")
    host = previous["machine"]
    handle = row["session_handle"]
    if handle not in {f"local-body:{actual.bridge}", f"fleet-body:{host}:{actual.bridge}"} or (
        row["creating_host"] and normalize_host(row["creating_host"]) != normalize_host(host)
    ):
        raise TaskError("retained native redrive execution host or handle changed")
    known, retired = native_bindings(conn, row, task)
    if native_conflict(actual.native, known, retired):
        raise TaskError("retained native redrive conflicts with native identity history")
    from .ordinary_native_redrive import redrive_invocation

    return {
        "version": 2, "task_id": task.id, "task_generation": task.generation,
        "reservation_key": row["key"], "session_id": actual.bridge,
        "native_session_id": actual.native, "worktree_id": actual.worktree, "machine": host,
        "startup_revision": row["startup_revision"],
        "invocation": redrive_invocation(row, host),
    }
