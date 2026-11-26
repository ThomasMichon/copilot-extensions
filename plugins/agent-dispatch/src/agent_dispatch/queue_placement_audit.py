"""Durable audit of fleet placement decisions as ordinary task events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .queue_records import TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue

PLACEMENT_NOTE_PREFIX = "fleet placement: "
PLACEMENT_NOTE_LIMIT = 16_000


def record_placement_decision(
    queue: TaskQueue, key: str, decision: Mapping[str, object], *, now: float | None = None,
) -> dict[str, object]:
    """Append one fleet placement decision to the reserved task's audit trail.

    The note is ``"fleet placement: " + <sorted JSON>``, so ``events <task>``
    shows the candidates, provider order and chosen host of this attempt.
    The status does not change; an identical repeat is not written twice.
    """
    if not isinstance(decision, Mapping):
        raise TaskError("placement decision must be an object")
    note = PLACEMENT_NOTE_PREFIX + json.dumps(
        {**decision, "reservation": key}, sort_keys=True, separators=(",", ":"),
    )
    if len(note) > PLACEMENT_NOTE_LIMIT:
        raise TaskError(f"placement decision exceeds {PLACEMENT_NOTE_LIMIT} characters")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT task_id, reserved_by FROM spawn_reservations WHERE key=?", (key,),
        ).fetchone()
        task = queue._fetch(conn, str(row["task_id"])) if row else None
        if row is None or task is None:
            raise TaskError("no such reservation")
        exists = conn.execute(
            "SELECT 1 FROM task_events WHERE task_id=? AND note=?", (task.id, note),
        ).fetchone()
        if exists is None:
            queue._audit(
                conn, task.id, ts=queue._now(now), from_status=task.status,
                to_status=task.status, worker=row["reserved_by"], note=note,
            )
        conn.commit()
    return {"task_id": task.id, "reservation": key, "recorded": exists is None}
