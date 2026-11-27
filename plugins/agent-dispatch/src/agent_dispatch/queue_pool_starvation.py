"""Pool lanes held only by tasks that stopped progressing (lane starvation).

A pool at capacity whose every running holder is suspended, submitted or
finished cannot start the queued work it permits until those holders are
cooled. The supervisor's dormant-body cooler frees most such lanes; this read
names the ones it has not freed, so the queue can surface them. It takes no
action.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .pool_instances import PoolInstanceDefinition
from .queue_common import Task
from .queue_pool_accountability import ReservationAccountability
from .queue_pool_placement import _permits, pool_lane_load
from .queue_records import SpawnState, Status

PROGRESSING = frozenset({Status.CLAIMED, Status.STARTED})


def starved_pools(
    conn: sqlite3.Connection, definitions: tuple[PoolInstanceDefinition, ...], *, now: float,
) -> list[dict[str, Any]]:
    """Full pools whose running holders all stopped progressing while permitted work waits."""
    accounting = ReservationAccountability(conn)
    queued = [
        Task._from_row(row) for row in conn.execute(
            "SELECT * FROM tasks WHERE status = ? AND hold_reason IS NULL AND awaiting_steer = 0"
            " AND not_before <= ?",
            (Status.QUEUED, now),
        )
    ]
    # Placement never moves a task off its retained pool, so it waits only there.
    retained = {
        row["task_id"]: (row["registration_id"], row["machine"], row["env"])
        for row in conn.execute(
            "SELECT task_id, registration_id, machine, env FROM task_pool_assignments"
            " WHERE assignment_released = 0",
        )
    }
    starved: list[dict[str, Any]] = []
    for pool in definitions:
        waiting = [
            task for task in queued
            if retained.get(task.id, pool.identity) == pool.identity and _permits(pool, task)
        ]
        if not waiting:
            continue
        load = pool_lane_load(conn, pool.identity, accounting=accounting)
        if load < pool.max_concurrent:
            continue
        holders = [
            row for row in conn.execute(
                "SELECT s.*, t.status AS holder_status FROM pool_reservation_bindings a"
                " JOIN spawn_reservations s ON s.key = a.reservation_key"
                " JOIN tasks t ON t.id = s.task_id"
                " WHERE a.registration_id = ? AND a.machine = ? AND a.env = ?"
                " ORDER BY s.reserved_at, s.key",
                pool.identity,
            )
            if row["state"] != SpawnState.COLD and accounting.accountable(row)
        ]
        if not holders or any(row["holder_status"] in PROGRESSING for row in holders):
            continue
        registration_id, machine, env = pool.identity
        starved.append({
            "registration_id": registration_id, "machine": machine, "env": env,
            "max_concurrent": pool.max_concurrent, "load": load,
            "holders": [
                {"task_id": row["task_id"], "task_status": row["holder_status"],
                 "reservation": row["key"], "state": row["state"]}
                for row in holders
            ],
            "queued": len(waiting),
            "oldest_queued_age": round(now - min(task.created_at for task in waiting), 3),
        })
    return starved
