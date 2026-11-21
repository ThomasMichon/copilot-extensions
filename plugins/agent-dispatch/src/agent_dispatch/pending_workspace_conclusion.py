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
from .supervisor_conclusion import _conclusion_retry_payload
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


def _bridge_native(row: sqlite3.Row, bridge: str) -> str | None:
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
    bridge = None
    if session.startswith("fleet-body:"):
        from .spawn_factories import _parse_fleet_body_handle
        fleet = _parse_fleet_body_handle(session)
        if fleet is None:
            return set()
        address, bridge = fleet
        hosts.add(normalize_host(address))
    elif session.startswith("local-body:"):
        bridge = session.removeprefix("local-body:")
    owner = row["owner"] or ""
    checkpoint = _conclusion_retry_payload({"conclusion_detail": row["conclusion_detail"]})
    retry_native = checkpoint.get("acp_session_id")
    if retry_native and not session:
        return set()
    if session:
        native = session
        if bridge is not None:
            native = _bridge_native(row, bridge)
            if not native:
                return set()
        if retry_native and retry_native != native:
            return set()
        if row["owner_session_id"] in (session, native):
            for worker in (row["completed_by"], owner):
                if worker and worker.partition("/")[2] != row["worktree"]:
                    return set()
                if machine := machine_from_owner(worker):
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
        if not hosts and (owner or row["completed_by"]) and row["owner_session_id"] not in (
            session, native,
        ):
            return set()
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
            f"r.driver,r.agent_info,r.conclusion_detail,t.owner,t.completed_by,t.owner_session_id,"
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
