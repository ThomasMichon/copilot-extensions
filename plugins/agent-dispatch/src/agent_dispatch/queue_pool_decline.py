"""Retain an explicit worker decline until its own workspace closeout settles."""

from __future__ import annotations

from collections.abc import Callable
import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from .queue_common import Task
from .queue_records import Status, TaskError


@dataclass(frozen=True)
class PoolWorkerDecline:
    task_id: str
    reservation_key: str
    generation: int
    startup_revision: int
    worker_id: str
    native_id: str
    exclusions: tuple[str, ...]
    note: str
    created_at: float
    updated_at: float
    blocker: str | None


@dataclass(frozen=True)
class PoolDeclineCloseout:
    receipt: dict
    applied: bool
    relationship: dict | None = None


class _QueueOwner(Protocol):
    def _connect(self) -> sqlite3.Connection: ...
    def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None: ...
    def _now(self, now: float | None) -> float: ...
    def task_relationship(self, task_id: str, *, event_id: int | None = None) -> dict | None: ...
    def reconcile_task_relationship(
        self, task_id: str, *, event_id: int | None = None,
        authority_guard: Callable[[], None] | None = None,
    ) -> dict: ...
    def _audit(
        self, conn: sqlite3.Connection, task_id: str, *, ts: float,
        from_status: str, to_status: str, worker: str | None, note: str,
    ) -> None: ...


def migrate_pool_decline(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pool_worker_declines ("
        " reservation_key TEXT PRIMARY KEY, task_id TEXT NOT NULL,"
        " generation INTEGER NOT NULL, startup_revision INTEGER NOT NULL,"
        " worker_id TEXT NOT NULL, native_id TEXT NOT NULL, exclusions TEXT NOT NULL,"
        " note TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,"
        " blocker TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pool_worker_decline_task"
        " ON pool_worker_declines(task_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pool_worker_decline_closeouts ("
        " reservation_key TEXT PRIMARY KEY, receipt TEXT NOT NULL)"
    )


def current_pool_decline(
    conn: sqlite3.Connection, task_id: str,
) -> PoolWorkerDecline | None:
    row = conn.execute(
        "SELECT d.* FROM pool_worker_declines d JOIN task_pool_assignments a"
        " ON a.reservation_key=d.reservation_key WHERE a.task_id=?", (task_id,),
    ).fetchone()
    if row is None:
        return None
    return PoolWorkerDecline(**{
        **dict(row), "exclusions": tuple(json.loads(row["exclusions"])),
    })


def recovered_pool_decline(
    conn: sqlite3.Connection, task_id: str, *, worker_id: str, native_id: str, generation: int,
) -> tuple[PoolWorkerDecline, str] | None:
    """Find this exact incarnation's decline only behind its committed closeout receipt.

    A replacement placement detaches the decline from the current assignment, so a
    lost reply must be recovered by its original binding rather than current state.
    """
    rows = conn.execute(
        "SELECT d.*, c.receipt, r.worktree AS reservation_worktree FROM pool_worker_declines d"
        " JOIN pool_worker_decline_closeouts c ON c.reservation_key=d.reservation_key"
        " JOIN spawn_reservations r ON r.key=d.reservation_key"
        " WHERE d.task_id=? AND d.worker_id=? AND d.native_id=? AND d.generation=?",
        (task_id, worker_id, native_id, generation),
    ).fetchall()
    if len(rows) != 1:
        return None
    row = dict(rows[0])
    receipt, worktree = json.loads(row.pop("receipt")), row.pop("reservation_worktree")
    decline = PoolWorkerDecline(**{**row, "exclusions": tuple(json.loads(row["exclusions"]))})
    binding = {
        "worker_id": worker_id, "native_id": native_id, "generation": generation,
        "startup_revision": decline.startup_revision, "reservation_key": decline.reservation_key,
    }
    if receipt.get("task_id") != task_id or receipt.get("binding") != binding or not worktree:
        return None
    return decline, worktree


def require_no_pool_decline(
    conn: sqlite3.Connection, task_id: str, *, reservation_key: str | None = None,
) -> None:
    decline = current_pool_decline(conn, task_id)
    if decline is None or reservation_key not in (None, decline.reservation_key):
        return
    task = conn.execute("SELECT status, generation FROM tasks WHERE id=?", (task_id,)).fetchone()
    if (
        task is not None and task["status"] == Status.CLAIMED
        and task["generation"] == decline.generation
    ):
        raise TaskError("worker decline retains the assignment pending own-workspace closeout")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise TaskError(f"worker decline requires a meaningful bounded {name}")
    return value.strip()


class PoolDeclineMixin:
    def close_pool_decline(
        self: _QueueOwner, task_id: str, worker_id: str, *, native_id: str,
        generation: int, startup_revision: int, reservation_key: str,
        now: float | None = None,
    ) -> PoolDeclineCloseout:
        """Observe through the owner runtime; never accept a caller's finalized JSON."""
        from .queue_pool_decline_closeout import close
        return close(
            self, task_id, worker_id=worker_id, native_id=native_id, generation=generation,
            startup_revision=startup_revision, reservation_key=reservation_key, now=now,
        )

    def settle_pool_decline(
        self: _QueueOwner, task_id: str, worker_id: str, *, native_id: str,
        generation: int, startup_revision: int, reservation_key: str,
    ) -> PoolDeclineCloseout:
        """Retry claim settlement only behind this exact binding's committed disposition."""
        from .queue_pool_decline_closeout import settle
        return settle(
            self, task_id, worker_id=worker_id, native_id=native_id, generation=generation,
            startup_revision=startup_revision, reservation_key=reservation_key,
        )

    def pool_worker_decline(
        self: _QueueOwner, task_id: str,
    ) -> PoolWorkerDecline | None:
        with self._connect() as conn:
            return current_pool_decline(conn, task_id)

    def recover_pool_decline(
        self: _QueueOwner, task_id: str, *, worker_id: str, native_id: str, generation: int,
    ) -> tuple[PoolWorkerDecline, str] | None:
        with self._connect() as conn:
            return recovered_pool_decline(
                conn, task_id, worker_id=worker_id, native_id=native_id, generation=generation,
            )

    def prepare_pool_decline(
        self: _QueueOwner, task_id: str, worker_id: str, *, native_id: str,
        generation: int, startup_revision: int, reservation_key: str,
        exclusions: list[str] | tuple[str, ...], note: str,
        now: float | None = None,
    ) -> PoolWorkerDecline:
        """Record a decision, not a yield, finalization receipt or native retirement."""
        note = _text(note, "explanation")
        if (
            type(generation) is not int or generation < 1
            or type(startup_revision) is not int or startup_revision < 1
            or not isinstance(exclusions, (list, tuple)) or not 1 <= len(exclusions) <= 3
            or any(not isinstance(value, str) for value in exclusions)
        ):
            raise TaskError("worker decline requires its exact incarnation and scoped exclusions")
        selected = tuple(sorted(set(exclusions)))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ts = self._now(now)
            task = self._fetch(conn, task_id)
            from .queue_pool_placement import pool_assignment, require_pool_start
            assignment = pool_assignment(conn, task_id)
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (reservation_key,),
            ).fetchone()
            if (
                not math.isfinite(ts) or task is None or assignment is None or row is None
                or task.status != Status.CLAIMED or task.owner is not None
                or task.hold_reason is not None or task.awaiting_steer
                or assignment.reservation_key != reservation_key
                or row["startup_revision"] != startup_revision or not row["startup_complete"]
            ):
                raise TaskError("worker decline requires its current unheld allocator claim")
            from .queue_execution_handoff import require_execution_admission
            require_execution_admission(conn, task_id)
            # Start and decline share the exact workspace/native admission fences,
            # but a recorded decline must not admit a later start.
            require_pool_start(conn, task, worker_id, native_id, generation, declining=True)
            allowed = {
                f"worktree:{row['worktree']}", f"pool:{assignment.registration_id}",
                f"machine:{assignment.machine}",
            }
            if not set(selected).issubset(allowed):
                raise TaskError("worker decline exclusions must name its own worktree, pool or machine")
            current = current_pool_decline(conn, task_id)
            if current is not None:
                if (
                    current.worker_id != worker_id or current.native_id != native_id
                    or current.generation != generation or current.startup_revision != startup_revision
                    or current.exclusions != selected or current.note != note
                ):
                    raise TaskError("worker decline cannot replace its original decision")
                return current
            from .queue_pool_startup import require_pool_decision
            require_pool_decision(conn, task, ts)
            conn.execute(
                "INSERT INTO pool_worker_declines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (reservation_key, task_id, generation, startup_revision, worker_id,
                 native_id, json.dumps(selected), note, ts, ts),
            )
            self._audit(
                conn, task_id, ts=ts, from_status=Status.CLAIMED, to_status=Status.CLAIMED,
                worker=worker_id, note=f"worker declined; own-workspace closeout pending: {note}",
            )
            result = current_pool_decline(conn, task_id)
            assert result is not None
            conn.commit()
            return result

    def record_pool_decline_blocker(
        self: _QueueOwner, task_id: str, worker_id: str, *, native_id: str,
        generation: int, startup_revision: int, reservation_key: str, detail: str,
        now: float | None = None,
    ) -> PoolWorkerDecline:
        """Retain a failed closeout visibly without applying its routing disposition."""
        detail = _text(detail, "closeout blocker")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = current_pool_decline(conn, task_id)
            task = self._fetch(conn, task_id)
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (reservation_key,),
            ).fetchone()
            if (
                current is None or task is None or row is None
                or current.reservation_key != reservation_key
                or type(generation) is not int or current.generation != generation
                or type(startup_revision) is not int or current.startup_revision != startup_revision
                or current.worker_id != worker_id or current.native_id != native_id
                or task.generation != generation or task.status != Status.CLAIMED
                or task.owner is not None or task.hold_reason is not None
                or row["startup_revision"] != startup_revision
            ):
                raise TaskError("worker decline closeout incarnation changed or is held")
            from .queue_pool_placement import require_pool_start
            require_pool_start(conn, task, worker_id, native_id, generation, declining=True)
            if current.blocker == detail:
                return current
            ts = self._now(now)
            if not math.isfinite(ts):
                raise TaskError("worker decline requires finite time")
            conn.execute(
                "UPDATE pool_worker_declines SET blocker=?, updated_at=? WHERE reservation_key=?",
                (detail, ts, reservation_key),
            )
            self._audit(
                conn, task_id, ts=ts, from_status=Status.CLAIMED, to_status=Status.CLAIMED,
                worker=worker_id, note=f"worker decline closeout blocked: {detail}",
            )
            result = current_pool_decline(conn, task_id)
            assert result is not None
            conn.commit()
            return result
