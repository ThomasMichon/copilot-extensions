"""Install the prospective policy boundary without taking over a legacy writer."""

from __future__ import annotations

import json
import sqlite3

from .queue_records import Status, TaskError


def _settled(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    from .queue_allocation_revisions import RETIRED_BEFORE_ALLOCATION
    from .queue_execution_custody import reservation_transport_accountable
    from .queue_task_relationships import _matches_receipt

    if (
        row["task_status"] not in Status.TERMINAL
        or row["state"] not in {"settled", "rearmed", "failed"}
        or not row["release_requested"]
        or row["native_release_pending"]
        or not (row["startup_complete"] or row["startup_fenced"])
        or row["cooling_token"] or row["cleanup_claim_token"]
        or row["conclusion_state"] in {"held", "pending"}
        or reservation_transport_accountable(conn, row)
    ):
        return False
    if row["task_relationship_event_id"] is None:
        return conn.execute(
            "SELECT 1 FROM spawn_reservations s WHERE key=? AND " + RETIRED_BEFORE_ALLOCATION,
            (row["key"],),
        ).fetchone() is not None
    event = conn.execute(
        "SELECT task_relationship FROM task_events WHERE task_id=? AND id=?",
        (row["task_id"], row["task_relationship_event_id"]),
    ).fetchone()
    if not event or not event[0]:
        return False
    value = json.loads(event[0])
    request, receipt = value["request"], value["receipt"]
    if value["state"] != "closed" or not _matches_receipt(receipt, request, phase="closed"):
        return False
    proof = receipt["producer"]["closeout"]["relationship_verification"]
    return bool(
        request["task_id"] == row["task_id"]
        and request["event_id"] == row["task_relationship_event_id"]
        and request["reservation_key"] == row["key"]
        and request["generation"] == row["task_relationship_generation"]
        and proof["retired"] is True and proof["quiescent"] is True
        and proof["subject"] == receipt["ref"]
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
        predicate = " AND s.ordinary_admission_policy IS NULL" if "ordinary_admission_policy" in columns else ""
        for row in conn.execute(
            "SELECT s.*, t.status AS task_status FROM spawn_reservations s LEFT JOIN tasks t ON t.id=s.task_id"
            " WHERE s.driver='ordinary-bridge' AND s.task_relationship_required=1" + predicate
        ):
            try:
                settled = _settled(conn, row)
            except (KeyError, TypeError, ValueError, TaskError):
                settled = False
            if not settled:
                raise TaskError(
                    f"settle legacy ordinary reservation {row['key']} on the original coordinator before upgrading"
                )
        if "ordinary_admission_policy" not in columns:
            conn.execute("ALTER TABLE spawn_reservations ADD COLUMN ordinary_admission_policy TEXT")
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
