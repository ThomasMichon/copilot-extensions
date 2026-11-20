"""Admission of a Dispatch-initiated operation against its durable reservation."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .queue_common import Task
from .queue_records import WorkerAdmissionInhibited

if TYPE_CHECKING:
    from .queue import TaskQueue


def bound_instance(conn: sqlite3.Connection, key: str) -> tuple[str, str, str] | None:
    binding = conn.execute(
        "SELECT registration_id,machine,env FROM pool_reservation_bindings WHERE reservation_key=?", (key,),
    ).fetchone()
    return (binding["registration_id"], binding["machine"], binding["env"]) if binding is not None else None


def require_retained(owner: TaskQueue, conn: sqlite3.Connection, task: Task, row: sqlite3.Row, *,
                     resume: bool = False) -> None:
    try:
        if resume:
            owner.require_worker_admission()
        if owner._spawn_admission is None:
            return
        owner._spawn_admission(task, row["reserved_by"], bound_instance(conn, row["key"]), row["driver"],
                               {"agent_info": json.loads(row["agent_info"]) if row["agent_info"] else None})
    except WorkerAdmissionInhibited as exc:
        if not resume:
            raise
        from .queue_execution_custody import PoolLaneUnavailable
        raise PoolLaneUnavailable("pool resume inhibited; retain its existing lane request") from exc
