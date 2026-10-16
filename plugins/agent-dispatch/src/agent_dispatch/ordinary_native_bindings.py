"""Native identity and retirement evidence for one retained allocation."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import identity
from .queue_records import SpawnState, TaskError


def native_bindings(conn: Any, row: Any, task: Any) -> tuple[list[str], list[str]]:
    """Classify handles using their producing driver, not the retry's driver."""
    known = {task.owner_session_id} if task.owner_session_id else set()
    retired: set[str] = set()
    classified_handles: set[str] = set()
    histories = conn.execute(
        "SELECT s.*,a.native_session_id FROM spawn_reservations s "
        "LEFT JOIN acquisition_receipts a ON a.reservation_key=s.key "
        "WHERE s.worktree=? AND (s.task_id=? OR s.creation_key=?)",
        (row["worktree"], task.id, row["creation_key"]),
    ).fetchall() if row["worktree"] else [row]
    unclassified: set[str] = set()
    for history in histories:
        handle = history["session_handle"]
        is_retired = bool(history["conversation_retired"]) or bool(
            handle and history["release_requested"]
            and history["state"] in {SpawnState.FAILED, SpawnState.SETTLED, SpawnState.REARMED}
        )
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
            known.add(native)
            if handle:
                classified_handles.add(handle)
            if is_retired:
                retired.add(native)
        elif handle and is_retired:
            raise TaskError("retired session handle has no proven native identity")
        elif handle and not handle.startswith("local-body:"):
            unclassified.add(handle)
    if unclassified - classified_handles:
        raise TaskError("retained bare session handle has no proven native identity")
    return sorted(known), sorted(retired)


def inherited_retirement(conn: Any, row: Any, task: Any) -> bool:
    """Permit inspection of a derived marker, never revival of its old native."""
    if not row["conversation_retired"] or row["agent_info"] or row["session_handle"]:
        return False
    _, retired = native_bindings(conn, row, task)
    if not retired:
        return False
    # A handleless explicit release has no attributable native to supersede.
    return not conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE worktree=? "
        "AND (task_id=? OR creation_key=?) AND conversation_retired=1 "
        "AND session_handle IS NULL AND release_requested=1 LIMIT 1",
        (row["worktree"], task.id, row["creation_key"]),
    ).fetchone()
