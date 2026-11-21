"""Read the conclusion owner's outstanding workspace work without claiming its retry."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from typing import cast

from .bridge_remote import normalize_host
from .current_driver_lookup import DriverMatch
from .ordinary_agent_facts import identity
from .queue_records import TaskError
from .tracking import machine_from_owner

_INDEX = "idx_pending_conclusion_workspace_v1"
_PENDING = "conclusion_state='pending'"
_CANDIDATE_LIMIT = 32


def install_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {_INDEX} ON spawn_reservations(worktree) WHERE {_PENDING}"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conclusion_attachment_target "
        "ON task_attachments(task_id,session_id,worktree_id,machine)"
    )


def _local_native(row: sqlite3.Row, bridge: str) -> str | None:
    natives: set[str] = set()
    if row["receipt_bridge"] is not None:
        if (row["receipt_bridge"], row["receipt_tree"], row["receipt_task"]) != (
            bridge, row["worktree"], row["task_id"],
        ):
            return None
        natives.add(row["receipt_native"])
    if row["agent_info"]:
        try:
            info = json.loads(row["agent_info"])
            if not isinstance(info, dict):
                return None
            if row["driver"] == "restricted-bridge":
                from .restricted_delivery import validate_receipt
                validate_receipt(info)
                if info["session_id"] != bridge:
                    return None
                natives.add(info["native_session_id"])
            else:
                body = identity(info)
                if body.bridge != bridge or body.worktree != row["worktree"]:
                    return None
                natives.add(body.native)
        except (TaskError, ValueError, TypeError, KeyError):
            return None
    return next(iter(natives)) if len(natives) == 1 else None


def _hosts(conn: sqlite3.Connection, row: sqlite3.Row) -> set[str]:
    hosts: set[str] = set()
    session = row["session_handle"] or ""
    recorded = row["execution_machine"]
    if recorded is not None:
        if not isinstance(recorded, str) or not recorded.strip():
            return set()
        hosts.add(normalize_host(recorded))
    if session.startswith("fleet-body:") and session.split(":")[1]:
        hosts.add(normalize_host(session.split(":")[1]))
    owner = row["owner"] or ""
    if row["worktree"] == owner.partition("/")[2] and (machine := machine_from_owner(owner)):
        hosts.add(normalize_host(machine))
    if session:
        native = session
        if session.startswith("local-body:"):
            native = _local_native(row, session.removeprefix("local-body:"))
            if not native:
                return set()
        if row["owner_session_id"] in (session, native):
            if machine := machine_from_owner(row["completed_by"] or owner):
                hosts.add(normalize_host(machine))
        attachments = conn.execute(
            "SELECT machine FROM task_attachments "
            "WHERE task_id=? AND session_id IN (?,?) AND worktree_id=? AND machine IS NOT NULL "
            "LIMIT ?", (row["task_id"], session, native, row["worktree"], _CANDIDATE_LIMIT + 1),
        )
        for i, (machine,) in enumerate(attachments):
            if i == _CANDIDATE_LIMIT:
                return set()
            if isinstance(machine, str) and machine.strip():
                hosts.add(normalize_host(machine))
    if not hosts and row["creating_host"]:
        hosts.add(normalize_host(row["creating_host"]))
    return hosts


def pending_workspace_conclusion(
    conn: sqlite3.Connection, *, machine: str, worktree: str,
) -> DriverMatch:
    """Inspect pending conclusions for this workspace in the caller's read snapshot."""
    host = normalize_host(machine)
    if not conn.in_transaction or not host or not worktree:
        raise ValueError("pending conclusion lookup requires a transaction and workspace identity")
    answer = DriverMatch.CLEAR
    with closing(conn.cursor()) as candidates:
        candidates.row_factory = cast(
            Callable[[sqlite3.Cursor, sqlite3.Row], object], sqlite3.Row,
        )
        candidates.execute(
            f"SELECT r.task_id,r.worktree,r.session_handle,r.execution_machine,r.creating_host,"  # noqa: S608 -- fixed SQL
            f"r.driver,r.agent_info,t.owner,t.completed_by,t.owner_session_id,"
            f"a.session_id receipt_bridge,a.native_session_id receipt_native,"
            f"a.worktree_id receipt_tree,a.task_id receipt_task"
            f" FROM spawn_reservations AS r INDEXED BY {_INDEX}"
            f" LEFT JOIN tasks AS t ON t.id=r.task_id"
            f" LEFT JOIN acquisition_receipts AS a ON a.reservation_key=r.key"
            f" WHERE {_PENDING} AND worktree=? LIMIT ?",
            (worktree, _CANDIDATE_LIMIT + 1),
        )
        for i, row in enumerate(candidates):
            if i == _CANDIDATE_LIMIT:
                return DriverMatch.UNKNOWN
            hosts = _hosts(conn, row)
            if hosts == {host}:
                return DriverMatch.MATCH
            if len(hosts) != 1:
                answer = DriverMatch.UNKNOWN
    return answer
