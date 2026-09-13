"""Small, dependency-free record types shared by :mod:`agent_dispatch.queue`
and :mod:`agent_dispatch.queue_schedule_registry`.

Extracted to its own module (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423) specifically to
give the four names below a real, importable home that neither of its two
consumers needs to reach back into the other for. ``queue.py`` composes
``queue_schedule_registry.ScheduleRegistrationMixin`` into ``TaskQueue``,
so a plain top-level ``from .queue import ...`` in the mixin module would
be a genuine load-time cycle; the previous fix (a lazy, function-local
import guarded by ``TYPE_CHECKING`` for static analysis) avoided that
cycle but left these names unresolvable to ``typing.get_type_hints()`` at
runtime, since they were never actually bound in
``queue_schedule_registry``'s module globals. Defining them here instead
-- a module with no dependency on either consumer -- lets both import them
as ordinary top-level names with no cycle and no runtime-introspection gap.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


class TaskError(RuntimeError):
    """Raised on an illegal state transition or a lease/ownership violation."""


@dataclass
class ScheduleRecord:
    """A read-only snapshot of a registered recurring-schedule row.

    ``entry`` is the schedule dict the timer producer consumes verbatim (the
    same shape a hand-authored spec's ``schedules[]`` entry has). Persisting it
    turns the formerly hand-edited JSON spec into a managed registry the
    coordinator owns, so recurring jobs can be registered / listed / inspected /
    removed as first-class objects.
    """

    id: str
    entry: dict
    paused: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ScheduleRecord:
        return cls(
            id=row["id"],
            entry=json.loads(row["spec"]),
            paused=bool(row["paused"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ScheduleLease:
    """A read-only snapshot of a schedule *job-lease* row.

    The job-lease elects a **single producer** for a scope (e.g. the fleet
    chronicler) -- the axis of "which machine runs the timer", distinct from the
    engine's per-task claim. It is **pin-not-failover**: a first writer wins the
    scope and renews it; a different caller is refused and must NOT steal it,
    even if the recorded lease looks stale. This deliberately does *not*
    reintroduce a wall-clock TTL takeover (the complement of the engine's
    liveness-not-lease task recovery); reassignment is an explicit operator act
    (:meth:`TaskQueue.release_schedule_lease` with ``force``). ``expires_at`` /
    ``renewed_at`` are recorded for *observability* only -- staleness is
    reported, never auto-transferred.
    """

    scope: str
    holder: str
    holder_session: str | None = None
    acquired_at: float = 0.0
    renewed_at: float = 0.0
    expires_at: float | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ScheduleLease:
        return cls(
            scope=row["scope"],
            holder=row["holder"],
            holder_session=row["holder_session"],
            acquired_at=row["acquired_at"],
            renewed_at=row["renewed_at"],
            expires_at=row["expires_at"],
        )


@dataclass(frozen=True)
class ResourceReservation:
    """An atomic producer reservation for one external logical resource."""

    key: str
    owner: str
    token: str
    task_id: str | None = None
    acquired_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ResourceReservation:
        return cls(
            key=row["key"],
            owner=row["owner"],
            token=row["token"],
            task_id=row["task_id"],
            acquired_at=row["acquired_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )
