"""Small, dependency-free record types shared by :mod:`agent_dispatch.queue`
and its extracted mixin modules (:mod:`agent_dispatch.queue_schedule_registry`,
:mod:`agent_dispatch.queue_routing_assignments`).

Extracted to its own module (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423) specifically to
give the names below a real, importable home that none of ``queue.py``'s
extracted mixins need to reach back into ``queue.py`` itself for.
``queue.py`` composes each mixin into ``TaskQueue``, so a plain top-level
``from .queue import ...`` in a mixin module would be a genuine load-time
cycle; an earlier fix (a lazy, function-local import guarded by
``TYPE_CHECKING`` for static analysis) avoided that cycle but left names
unresolvable to ``typing.get_type_hints()`` at runtime, since they were
never actually bound in the mixin module's own globals. Defining them here
instead -- a module with no dependency on any consumer -- lets every
consumer import them as ordinary top-level names with no cycle and no
runtime-introspection gap.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


class TaskError(RuntimeError):
    """Raised on an illegal state transition or a lease/ownership violation."""


class Status:
    """The eight task states (string constants, stored verbatim)."""

    PROPOSED = "proposed"
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTED = "started"
    #: Previously started, owner-preserving, dormant, and non-claimable.
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    #: Terminal failure: a held task requeued too many times (its owner kept
    #: going gone) -- an actionable dead-letter end state rather than churning
    #: crash -> gone -> requeue forever.
    DEAD_LETTER = "dead_letter"

    #: States a worker actively holds; recoverable by liveness GC (owner-gone).
    HELD = frozenset({CLAIMED, STARTED})
    #: Non-terminal states that retain an owner. Suspended tasks are deliberately
    #: excluded from HELD because they have no active lease or embodiment.
    OWNED = frozenset({CLAIMED, STARTED, SUSPENDED})
    #: Terminal states -- no further transitions.
    TERMINAL = frozenset({COMPLETED, ABANDONED, DEAD_LETTER})
    #: Non-terminal states from which an abandon (with permission) is allowed.
    ABANDONABLE = frozenset({PROPOSED, QUEUED, CLAIMED, STARTED, SUSPENDED})


class SpawnState:
    """The lifecycle states of a spawn reservation."""

    #: Reserved; this spawner owns the (task, attempt) spawn but embody has not
    #: yet been launched (or its handle not yet recorded). A restart reconciles
    #: a reservation stuck here (spawn confirmed -> ``spawned``/``settled``, or
    #: lost -> ``failed`` so a fresh attempt can be reserved).
    RESERVING = "reserving"
    #: Embody launched; the session/worktree handle is recorded.
    SPAWNED = "spawned"
    #: The headless body was stopped intentionally while its task is suspended.
    #: The reservation remains the durable prior-body handle and prevents a
    #: replacement until an explicit resume request releases it.
    COLD = "cold"
    #: The body has stopped or failed and this reservation is concluding the
    #: exact allocation it created. The reservation remains active only until
    #: exact body absence is proven; allocation cleanup then remains visible as
    #: conclusion metadata without fencing replacement capacity.
    RELEASING = "releasing"
    #: The reserved (task, attempt) reached a terminal outcome and needs no
    #: further spawning.
    SETTLED = "settled"
    #: The spawn failed (or was lost); a fresh attempt may now be reserved.
    FAILED = "failed"
    #: A failed attempt was explicitly retired by an operator rearm. The row
    #: remains queryable for audit, but no longer counts toward dead-lettering.
    REARMED = "rearmed"
    #: The spawn did not fail -- it declined because a carried session from a
    #: prior attempt (same exclusive key) was confirmed still live/busy, not
    #: gone. This is a legitimate "not yet safe to touch" answer, never an
    #: error: a fresh attempt is immediately eligible next cycle, and (unlike
    #: FAILED) it never counts toward dead-lettering (see
    #: :meth:`Supervisor._failed_spawn_counts`). Distinguishing this from
    #: FAILED is what lets a busy carried session's owner keep working while
    #: the next attempt safely re-checks liveness, instead of the task being
    #: dead-lettered by attempts it never actually failed.
    DEFERRED = "deferred"

    #: States in which a reservation still "owns" the task's spawn -- no new
    #: attempt may be reserved while one of these is outstanding.
    ACTIVE = frozenset({RESERVING, SPAWNED, COLD, RELEASING})
    #: States a reservation may be released from (a new attempt is allowed).
    RELEASABLE = frozenset({SETTLED, FAILED, REARMED, DEFERRED})


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


@dataclass(frozen=True)
class SpawnReservation:
    """A read-only snapshot of a spawn-reservation row."""

    key: str
    task_id: str
    exclusive_key: str | None
    attempt: int
    state: str
    reserved_by: str | None = None
    session_handle: str | None = None
    worktree: str | None = None
    inherited_worktree: str | None = None
    worktree_ownership: str | None = None
    creating_host: str | None = None
    driver: str | None = None
    release_requested: bool = False
    release_disposition: str | None = None
    detail: str | None = None
    conclusion_state: str | None = None
    conclusion_detail: str | None = None
    cleanup_claim_token: str | None = None
    cleanup_claim_expires_at: float | None = None
    reserved_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> SpawnReservation:
        return cls(
            key=row["key"],
            task_id=row["task_id"],
            exclusive_key=row["exclusive_key"],
            attempt=row["attempt"],
            state=row["state"],
            reserved_by=row["reserved_by"],
            session_handle=row["session_handle"],
            worktree=row["worktree"],
            inherited_worktree=row["inherited_worktree"],
            worktree_ownership=row["worktree_ownership"],
            creating_host=row["creating_host"],
            driver=row["driver"],
            release_requested=bool(row["release_requested"]),
            release_disposition=row["release_disposition"],
            detail=row["detail"],
            conclusion_state=row["conclusion_state"],
            conclusion_detail=row["conclusion_detail"],
            cleanup_claim_token=row["cleanup_claim_token"],
            cleanup_claim_expires_at=row["cleanup_claim_expires_at"],
            reserved_at=row["reserved_at"],
            updated_at=row["updated_at"],
        )
