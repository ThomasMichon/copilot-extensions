"""Capture original cohort evidence in the assignment owner's existing audit."""

from __future__ import annotations

from dataclasses import asdict
import sqlite3

from .queue_common import Task
from .queue_pool_placement import pool_assignment
from .queue_producer_receipts import task_producer_receipt


def capture_cohort(
    conn: sqlite3.Connection, task: Task, *, reservation_key: str | None,
) -> dict:
    """Snapshot known original references under the caller's assignment transaction."""
    assignment = pool_assignment(conn, task.id, include_released=True)
    return {
        "schema": "dispatch-assignment-cohort",
        "version": 1,
        "emitter": task_producer_receipt(conn, task),
        "pool": (
            asdict(assignment)
            if assignment is not None and reservation_key is not None
            and assignment.reservation_key == reservation_key else None
        ),
    }
