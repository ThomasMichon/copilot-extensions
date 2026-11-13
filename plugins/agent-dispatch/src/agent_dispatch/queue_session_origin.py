"""Retain session-birth evidence separately from reusable workspace provenance."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .events import EventBus
from .queue_assignment_cohort import capture_cohort
from .queue_common import Task
from .queue_records import SpawnReservation, SpawnState, TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue


class BindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    startup_revision: int = Field(strict=True, ge=1)
    transport: Literal["baseline", "origin-v1"]


def migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(spawn_reservations)")}
    for name, declaration in (
        ("session_origin", "TEXT"),
        ("session_origin_reservation_key", "TEXT"),
        ("session_origin_transport", "TEXT DEFAULT 'baseline'"),
    ):
        if name not in columns:
            try:
                conn.execute(f"ALTER TABLE spawn_reservations ADD COLUMN {name} {declaration}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_session_origin_retained"
        " BEFORE UPDATE OF session_origin, session_origin_reservation_key ON spawn_reservations"
        " WHEN OLD.session_origin_reservation_key IS NOT NULL AND"
        " (NEW.session_origin IS NOT OLD.session_origin OR"
        " NEW.session_origin_reservation_key IS NOT OLD.session_origin_reservation_key)"
        " BEGIN SELECT RAISE(ABORT, 'original session origin is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_session_transport_retained"
        " BEFORE UPDATE OF session_origin_transport ON spawn_reservations"
        " WHEN OLD.session_origin_transport IS NOT NULL AND"
        " NEW.session_origin_transport IS NOT OLD.session_origin_transport"
        " BEGIN SELECT RAISE(ABORT, 'session origin transport is immutable'); END"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_spawn_session_creation"
        " ON spawn_reservations(creation_key, driver)"
    )


def retain(conn: sqlite3.Connection, task: Task, *, key: str) -> None:
    """Run after the matching pool claim, inside the original reservation commit."""
    row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
    prior = None
    if row["driver"] == "ordinary-bridge":
        # Ordinary create replays the allocation's creation key, even after a
        # lost reply. Acquisition instead owns a distinct delivery per attempt.
        prior = conn.execute(
            "SELECT * FROM spawn_reservations WHERE creation_key=?"
            " AND driver='ordinary-bridge' AND key<>? ORDER BY rowid LIMIT 1",
            (row["creation_key"], key),
        ).fetchone()
    if prior is None:
        original_key = key
        encoded = json.dumps({
            "task_id": task.id, "reservation_key": key,
            "cohort": capture_cohort(conn, task, reservation_key=key),
        }, sort_keys=True)
        transport = None
    else:
        original_key = prior["session_origin_reservation_key"] or prior["key"]
        original = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (original_key,),
        ).fetchone()
        if original is None:
            raise TaskError("original session reservation is unavailable")
        encoded, transport = original["session_origin"], original["session_origin_transport"]
    conn.execute(
        "UPDATE spawn_reservations SET session_origin=?, session_origin_reservation_key=?,"
        " session_origin_transport=? WHERE key=?", (encoded, original_key, transport, key),
    )


def _representable(row: sqlite3.Row) -> bool:
    from .worktree_cohort import _carrier_json

    value = json.loads(row["session_origin"]) if row["session_origin"] else None
    if (
        not isinstance(value, dict) or set(value) != {"task_id", "reservation_key", "cohort"}
        or value["task_id"] != row["task_id"] or value["reservation_key"] != row["key"]
        or _carrier_json(value["cohort"]) is None
    ):
        return False
    cohort = value["cohort"]
    return (
        (cohort["emitter"] is None or cohort["emitter"]["task_id"] == value["task_id"])
        and (cohort["pool"] is None or (
            cohort["pool"]["task_id"] == value["task_id"]
            and cohort["pool"]["reservation_key"] == value["reservation_key"]
        ))
    )


def bind(
    queue: TaskQueue, key: str, *, startup_revision: int, transport: str,
) -> SpawnReservation:
    """Seal the original wire choice before any independently owned native send."""
    if transport not in {"baseline", "origin-v1"}:
        raise TaskError("unsupported session origin transport")
    if type(startup_revision) is not int or startup_revision < 1:
        raise TaskError("session origin binding requires a startup revision")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        if row is None:
            raise TaskError(f"no such reservation: {key}")
        if (
            row["state"] != SpawnState.RESERVING or row["release_requested"]
            or row["startup_fenced"] or row["startup_revision"] != startup_revision
        ):
            raise TaskError("session origin binding requires the current reserving startup")
        original_key = row["session_origin_reservation_key"] or key
        original = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (original_key,),
        ).fetchone()
        if original is None or (
            original_key != key and (
                row["driver"] != "ordinary-bridge" or original["driver"] != row["driver"]
                or original["creation_key"] != row["creation_key"]
            )
        ):
            raise TaskError("session origin binding has no original creation owner")
        retained = original["session_origin_transport"]
        if retained is not None and retained != transport:
            raise TaskError("original session origin transport cannot change")
        if transport == "origin-v1" and not _representable(original):
            raise TaskError("session origin transport requires representable original evidence")
        for selected in {original_key, key}:
            conn.execute(
                "UPDATE spawn_reservations SET session_origin_transport=? WHERE key=?",
                (transport, selected),
            )
        updated = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(updated)


def register_routes(app: FastAPI, queue: TaskQueue, bus: EventBus) -> None:
    @app.post("/spawn-reservations/{key}/session-origin")
    def bind_origin(key: str, body: BindingBody) -> dict:
        try:
            result = asdict(bind(queue, key, **body.model_dump()))
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "spawn.session_origin_bound", "reservation": result})
        return result
