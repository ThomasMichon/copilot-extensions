"""Native identity and retirement evidence for one retained allocation."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import identity
from .queue_records import SpawnState, TaskError


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
        native = history["native_session_id"] if "native_session_id" in history.keys() else None
        if history["agent_info"]:
            actual = identity(json.loads(history["agent_info"])).native
            if native and actual != native:
                raise TaskError("retained native identity evidence conflicts")
            native = actual
        if history["driver"] == "cli" and handle and not handle.startswith("local-body:"):
            if native and native != handle:
                raise TaskError("retained CLI native identity evidence conflicts")
            native = handle
        if native:
            if handle:
                if handle in handle_natives and handle_natives[handle] != native:
                    raise TaskError("retained session handle has conflicting native identities")
                handle_natives[handle] = native
        evidence.append((history, native))
    for history, native in evidence:
        handle = history["session_handle"]
        native = native or handle_natives.get(handle)
        is_retired = bool(history["conversation_retired"]) or bool(
            history["release_requested"]
            and history["state"] in {SpawnState.FAILED, SpawnState.SETTLED, SpawnState.REARMED}
        )
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
