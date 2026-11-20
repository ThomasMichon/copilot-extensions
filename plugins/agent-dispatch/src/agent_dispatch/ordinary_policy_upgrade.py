"""Install the prospective policy boundary without taking over a legacy writer."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from .queue_records import Status, TaskError

if TYPE_CHECKING:
    from .queue_execution_custody import ReservationTransportSnapshot


def _settled(
    conn: sqlite3.Connection, row: sqlite3.Row, transport: ReservationTransportSnapshot
) -> bool:
    from .queue_allocation_revisions import RETIRED_WITHOUT_WORKSPACE
    from .queue_relationship_continuation import current_binding
    from .queue_task_relationships import _matches_receipt

    if (
        row["task_status"] not in Status.TERMINAL
        or row["state"] not in {"settled", "rearmed", "failed"}
        or not row["release_requested"]
        or row["native_release_pending"]
        or not (row["startup_complete"] or row["startup_fenced"])
        or row["cooling_token"]
        or row["cleanup_claim_token"]
        or row["conclusion_state"] in {"held", "pending"}
        or transport.accountable(row)
    ):
        return False
    task, key = row["task_id"], row["key"]
    event_id = row["task_relationship_event_id"]
    if event_id is None:
        if not conn.execute(
            "SELECT 1 FROM spawn_reservations s WHERE key=? AND " + RETIRED_WITHOUT_WORKSPACE,
            (row["key"],),
        ).fetchone():
            return False
        if row["allocation_project"] is None:
            return True
        events = conn.execute(
            "SELECT DISTINCT task_relationship_event_id FROM spawn_reservations"
            " WHERE task_id=? AND allocation_reservation_key=? AND task_relationship_event_id IS NOT NULL LIMIT 2",
            (task, row["allocation_reservation_key"]),
        ).fetchall()
        if len(events) != 1:
            return False
        event_id = events[0][0]
    event = conn.execute(
        "SELECT task_relationship FROM task_events WHERE task_id=? AND id=?",
        (task, event_id),
    ).fetchone()
    if not event or not event[0]:
        return False
    value = json.loads(event[0])
    request, receipt = value["request"], value["receipt"]
    if (
        not isinstance(request, dict)
        or value["state"] != "closed"
        or not _matches_receipt(receipt, request, phase="closed")
    ):
        return False
    proof = receipt["producer"]["closeout"]["relationship_verification"]
    if not (
        request["task_id"] == task
        and request["event_id"] == event_id
        and proof["retired"] is True
        and proof["quiescent"] is True
        and proof["subject"] == receipt["ref"]
    ):
        return False
    binding = current_binding(value)
    if binding["reservation_key"] == request["reservation_key"] == key and row[
        "allocation_reservation_key"
    ] in (None, key):
        return (
            request["generation"] == row["task_relationship_generation"]
            and "continuation" not in value
        )
    return _settled_retry(conn, row, value)


def _settled_retry(conn: sqlite3.Connection, row: sqlite3.Row, value: dict[str, Any]) -> bool:
    from .managed_assignment_proof import managed_allocation
    from .ordinary_native_bindings import reservation_native_identity
    from .queue_relationship_continuation import current_binding

    task, key = row["task_id"], row["key"]
    request, binding = value["request"], current_binding(value)
    execution = conn.execute(
        "SELECT * FROM spawn_reservations WHERE task_id=? AND key=?",
        (task, binding["reservation_key"]),
    ).fetchone()
    if execution is None:
        return False
    allocation = managed_allocation(conn, task, execution, value)
    # Each participant must pass its own legacy retirement/transport check. A
    # later policy-qualified row cannot serve as evidence for an unknown writer.
    if (
        "ordinary_admission_policy" in row.keys()
        and conn.execute(
            "SELECT 1 FROM spawn_reservations WHERE key IN (?,?,?) AND ordinary_admission_policy IS NOT NULL",
            (allocation["key"], request["reservation_key"], execution["key"]),
        ).fetchone()
    ):
        return False
    if row["task_relationship_event_id"] is None:
        from .queue_allocation_revisions import recovered_empty
        return recovered_empty(conn, row, request["reservation_key"])
    if key != execution["key"]:
        return (
            key == request["reservation_key"]
            and not reservation_native_identity(conn, row)
            and not conn.execute(
                "SELECT 1 FROM acquisition_markers WHERE reservation_key=?", (key,)
            ).fetchone()
        )
    return _executing_native(conn, row, binding["generation"], allocation)


def _executing_native(
    conn: sqlite3.Connection, row: sqlite3.Row, generation: int, allocation: dict[str, Any]
) -> bool:
    from .ordinary_agent_facts import identity
    from .ordinary_native_bindings import reservation_native_identity
    from .ordinary_pool_startup import created_workspace_record
    from .ordinary_writer_admission import writer_requirement

    task, key = row["task_id"], row["key"]
    info = json.loads(row["agent_info"])
    if not isinstance(info, dict):
        return False
    record = created_workspace_record({"id": task}, allocation, info)
    agent = identity(info)
    requirement = writer_requirement(info.get("worktree_writer_requirement"))
    acquired = conn.execute(
        "SELECT * FROM acquisition_receipts WHERE reservation_key=?", (key,)
    ).fetchone()
    if acquired is None:
        if conn.execute(
            "SELECT 1 FROM acquisition_markers WHERE reservation_key=?", (key,)
        ).fetchone():
            return False
    elif (
        acquired["task_id"],
        acquired["task_generation"],
        acquired["worktree_id"],
        acquired["session_id"],
        acquired["native_session_id"],
    ) != (task, generation, agent.worktree, agent.bridge, agent.native):
        return False
    return bool(
        type(info.get("task_generation")) is int
        and info["task_generation"] == generation
        and row["session_handle"] == f"local-body:{agent.bridge}"
        and reservation_native_identity(conn, row) == agent.native
        and (agent.project, agent.worktree)
        == (allocation["allocation_project"], allocation["worktree"])
        and (agent.path is None or agent.path == allocation["worktree_path"])
        and record.get("creation_nonce") == allocation["creation_nonce"]
        and requirement == allocation["worktree_writer_requirement"]
        and requirement["creation_nonce"] == allocation["creation_nonce"]
    )


def install_policy_boundary(conn: sqlite3.Connection) -> None:
    """Run before other migrations on upgrade; refuse without any schema change."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(spawn_reservations)")}
    if "task_relationship_required" not in columns:
        return  # Fresh/pre-relationship schema: called again after table creation.
    if conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='trigger' AND name='ordinary_policy_admit_update'"
    ).fetchone():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(spawn_reservations)")}
        predicate = (
            " AND s.ordinary_admission_policy IS NULL"
            if "ordinary_admission_policy" in columns
            else ""
        )
        transport = None
        for row in conn.execute(
            "SELECT s.*, t.status AS task_status FROM spawn_reservations s LEFT JOIN tasks t ON t.id=s.task_id"
            " WHERE s.driver='ordinary-bridge' AND s.task_relationship_required=1" + predicate
        ):
            if transport is None:
                from .queue_execution_custody import ReservationTransportSnapshot

                transport = ReservationTransportSnapshot(conn)
            try:
                settled = _settled(conn, row, transport)
            except (KeyError, TypeError, ValueError, TaskError):
                settled = False
            if not settled:
                raise TaskError(
                    f"settle legacy ordinary reservation {row['key']} on the original coordinator before upgrading"
                )
        if "ordinary_admission_policy" not in columns:
            conn.execute(
                "ALTER TABLE spawn_reservations ADD COLUMN ordinary_admission_policy TEXT"
            )
        # A concurrently running old writer may not create another unattributed
        # governed row after the upgrade check commits.
        for operation, action in (
            ("insert", "INSERT"),
            ("update", "UPDATE OF driver, task_relationship_required, ordinary_admission_policy"),
        ):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS ordinary_policy_admit_{operation} BEFORE {action} ON spawn_reservations"
                " WHEN NEW.driver='ordinary-bridge' AND NEW.task_relationship_required=1 AND NEW.ordinary_admission_policy IS NULL"
                " BEGIN SELECT RAISE(ABORT,'ordinary capture requires a stable admission policy'); END"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
