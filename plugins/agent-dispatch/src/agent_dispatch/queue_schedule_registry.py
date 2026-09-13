"""``TaskQueue`` mixin: schedule registry, supervisor registrations, schedule
job-leases, and external producer resource reservations.

Extracted from :mod:`agent_dispatch.queue` (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2357) as the next
natural table cluster after the CLI-command split that produced
``loop_commands.py``: these four concerns (recurring schedules, supervisor
registrations, single-producer job-leases, and external resource
reservations) share no state with the task/spawn/routing machinery in
``queue.py`` beyond the connection helper and the shared record dataclasses,
and none of them is monkeypatched by name anywhere in the test suite -- so
this is a plain mixin, not a proxy-forwarding split like
``loop_commands.py``'s CLI helpers.

:class:`ScheduleRegistrationMixin` is composed into
:class:`agent_dispatch.queue.TaskQueue` via multiple inheritance; it relies
on ``self._connect()`` and ``self._now()`` from that class and is not
usable standalone.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import TYPE_CHECKING

from .registrations import (
    RegistrationError,
    RegistrationKind,
    RegistrationRecord,
    RegistrationStatus,
    derive_registration_id,
    validate_registration,
)

if TYPE_CHECKING:
    # Type-checking-only: the real values come back from ``.queue`` via lazy,
    # function-local imports (see each method below) to avoid a real circular
    # import at module-load time -- ``queue.py`` imports this module's
    # ``ScheduleRegistrationMixin`` for ``TaskQueue`` to inherit from.
    from .queue import ResourceReservation, ScheduleLease, ScheduleRecord


class ScheduleRegistrationMixin:
    """Schedule registry, supervisor registration, schedule-lease, and
    external resource-reservation methods for :class:`TaskQueue`."""

    # -- schedule registry ---------------------------------------------------

    def register_schedule(self, entry: dict, *, now: float | None = None) -> ScheduleRecord:
        """Register (or update) a recurring schedule by its ``id``.

        ``entry`` is a timer-producer schedule dict; it is validated eagerly
        (id + title + a resolvable lane + exactly one valid cadence) so a
        malformed schedule is rejected at register time rather than silently
        failing every tick. Re-registering the same ``id`` upserts the spec
        (preserving ``created_at`` and the ``paused`` flag).
        """
        from .producers.schedule import ScheduleError, due_occurrences
        from .queue import ScheduleRecord, TaskError

        sid = entry.get("id")
        if not sid or not str(sid).strip():
            raise TaskError("schedule needs a non-empty 'id'")
        if not str(entry.get("title") or "").strip():
            raise TaskError(f"schedule {sid!r} needs a 'title'")
        if not entry.get("repo"):
            raise TaskError(f"schedule {sid!r} needs a 'repo' (the task lane)")
        try:
            due_occurrences(entry, now=self._now(now))
        except ScheduleError as exc:
            raise TaskError(str(exc)) from exc

        ts = self._now(now)
        spec = json.dumps(entry)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT id FROM schedules WHERE id = ?", (sid,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE schedules SET spec = ?, updated_at = ? WHERE id = ?",
                    (spec, ts, sid),
                )
            else:
                conn.execute(
                    "INSERT INTO schedules (id, spec, paused, created_at, updated_at) "
                    "VALUES (?, ?, 0, ?, ?)",
                    (sid, spec, ts, ts),
                )
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
            conn.execute("COMMIT")
        return ScheduleRecord._from_row(row)

    def list_schedules(self, *, include_paused: bool = True) -> list[ScheduleRecord]:
        """List registered schedules, ordered by id."""
        from .queue import ScheduleRecord

        query = "SELECT * FROM schedules"
        if not include_paused:
            query += " WHERE paused = 0"
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [ScheduleRecord._from_row(r) for r in rows]

    def get_schedule(self, sid: str) -> ScheduleRecord | None:
        """Return one registered schedule by id, or ``None``."""
        from .queue import ScheduleRecord

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
        return ScheduleRecord._from_row(row) if row else None

    def remove_schedule(self, sid: str) -> bool:
        """Delete a registered schedule; return whether a row was removed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (sid,))
        return cur.rowcount > 0

    def set_schedule_paused(
        self, sid: str, paused: bool, *, now: float | None = None
    ) -> ScheduleRecord:
        """Pause/resume a schedule (a paused schedule is skipped by the registry
        tick but retains its definition). Raises if the schedule is unknown."""
        from .queue import ScheduleRecord, TaskError

        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM schedules WHERE id = ?", (sid,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such schedule: {sid}")
            conn.execute(
                "UPDATE schedules SET paused = ?, updated_at = ? WHERE id = ?",
                (1 if paused else 0, ts, sid),
            )
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
            conn.execute("COMMIT")
        return ScheduleRecord._from_row(row)

    # -- supervisor registrations --------------------------------------------

    @staticmethod
    def _registration_from_row(row: sqlite3.Row) -> RegistrationRecord:
        return RegistrationRecord(
            id=row["id"],
            kind=row["kind"],
            spec=json.loads(row["spec"]),
            machine=row["machine"],
            env=row["env"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def register_registration(
        self,
        kind: str,
        spec: dict,
        *,
        reg_id: str | None = None,
        machine: str | None = None,
        env: str = "default",
        now: float | None = None,
    ) -> RegistrationRecord:
        """Register (or upsert) a supervision unit; return its handle.

        ``kind`` and ``spec`` are validated eagerly (see
        :func:`registrations.validate_registration`) so a malformed unit is
        refused here rather than failing every reconcile. The id is the caller's
        explicit ``reg_id`` or a value **derived deterministically** from
        ``(kind, machine, env, spec)`` -- so re-registering the same unit
        **upserts** (idempotent by handle) rather than duplicating it, preserving
        ``created_at`` and the ``status`` flag across the upsert.
        """
        from .queue import TaskError

        if kind not in RegistrationKind.DIRECT:
            raise TaskError(
                f"registration kind {kind!r} is not available through direct registration"
            )
        try:
            validate_registration(kind, spec)
        except RegistrationError as exc:
            raise TaskError(str(exc)) from exc
        env = env or "default"
        rid = reg_id or derive_registration_id(kind, spec, machine, env)
        ts = self._now(now)
        try:
            spec_json = json.dumps(spec)
        except TypeError as exc:
            raise TaskError(f"registration 'spec' is not JSON-serializable: {exc}") from exc
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT id FROM registrations WHERE id = ?", (rid,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE registrations SET kind = ?, spec = ?, machine = ?, "
                    "env = ?, updated_at = ? WHERE id = ?",
                    (kind, spec_json, machine, env, ts, rid),
                )
            else:
                conn.execute(
                    "INSERT INTO registrations "
                    "(id, kind, spec, machine, env, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, kind, spec_json, machine, env, RegistrationStatus.ACTIVE, ts, ts),
                )
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (rid,)).fetchone()
            conn.execute("COMMIT")
        return self._registration_from_row(row)

    def list_registrations(
        self,
        *,
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[RegistrationRecord]:
        """List registrations, optionally filtered by kind / machine / env,
        ordered by id."""
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if machine is not None:
            clauses.append("machine = ?")
            params.append(machine)
        if env is not None:
            clauses.append("env = ?")
            params.append(env)
        if not include_paused:
            clauses.append("status != ?")
            params.append(RegistrationStatus.PAUSED)
        query = "SELECT * FROM registrations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._registration_from_row(r) for r in rows]

    def get_registration(self, rid: str) -> RegistrationRecord | None:
        """Return one registration by id, or ``None``."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (rid,)).fetchone()
        return self._registration_from_row(row) if row else None

    def remove_registration(self, rid: str) -> bool:
        """Delete a registration; return whether a row was removed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM registrations WHERE id = ?", (rid,))
        return cur.rowcount > 0

    def set_registration_status(
        self, rid: str, status: str, *, now: float | None = None
    ) -> RegistrationRecord:
        """Set a registration's lifecycle status (e.g. pause/resume). Raises if
        the id is unknown or the status is invalid."""
        from .queue import TaskError

        if status not in RegistrationStatus.ALL:
            raise TaskError(
                f"invalid registration status {status!r}; expected one of "
                f"{', '.join(sorted(RegistrationStatus.ALL))}"
            )
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM registrations WHERE id = ?", (rid,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such registration: {rid}")
            conn.execute(
                "UPDATE registrations SET status = ?, updated_at = ? WHERE id = ?",
                (status, ts, rid),
            )
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (rid,)).fetchone()
            conn.execute("COMMIT")
        return self._registration_from_row(row)

    # -- schedule job-leases (single-producer election) ----------------------

    def acquire_schedule_lease(
        self,
        scope: str,
        holder: str,
        *,
        holder_session: str | None = None,
        ttl: float | None = None,
        now: float | None = None,
    ) -> tuple[ScheduleLease, bool]:
        """Acquire or renew the job-lease for ``scope`` (pin-not-failover).

        Returns ``(lease, granted)``. A first writer wins the scope
        (``granted=True``); the same ``holder`` renews it (``granted=True``,
        refreshing ``renewed_at``/``expires_at``); a **different** caller is
        refused (``granted=False``) and MUST NOT run the scope's producer --
        the recorded lease is never auto-stolen, even when stale. This elects a
        single producer machine (e.g. the fleet chronicler on one host) without
        a wall-clock takeover. ``ttl`` only sets ``expires_at`` for
        observability; it does not enable a takeover.
        """
        from .queue import ScheduleLease

        ts = self._now(now)
        expires_at = (ts + ttl) if ttl else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schedule_leases "
                    "(scope, holder, holder_session, acquired_at, renewed_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, holder, holder_session, ts, ts, expires_at),
                )
                granted = True
            elif row["holder"] == holder:
                conn.execute(
                    "UPDATE schedule_leases SET "
                    "holder_session = COALESCE(?, holder_session), "
                    "renewed_at = ?, expires_at = ? WHERE scope = ?",
                    (holder_session, ts, expires_at, scope),
                )
                granted = True
            else:
                granted = False
            row = conn.execute(
                "SELECT * FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
            conn.execute("COMMIT")
        return ScheduleLease._from_row(row), granted

    def release_schedule_lease(
        self, scope: str, holder: str, *, force: bool = False, now: float | None = None
    ) -> bool:
        """Release the job-lease for ``scope``. The current holder may release
        its own lease; ``force=True`` lets an operator reassign a lease held by
        a different (e.g. retired) holder. Returns whether a lease was removed;
        raises if a non-holder tries to release without ``force``."""
        from .queue import TaskError

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT holder FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            if not force and row["holder"] != holder:
                conn.execute("COMMIT")
                raise TaskError(
                    f"lease {scope!r} is held by {row['holder']!r}, not {holder!r} "
                    "(use force to reassign)"
                )
            conn.execute("DELETE FROM schedule_leases WHERE scope = ?", (scope,))
            conn.execute("COMMIT")
        return True

    def get_schedule_lease(self, scope: str) -> ScheduleLease | None:
        """Return the job-lease for ``scope``, or ``None`` if unheld."""
        from .queue import ScheduleLease

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
        return ScheduleLease._from_row(row) if row else None

    def list_schedule_leases(self) -> list[ScheduleLease]:
        """List all held job-leases, ordered by scope."""
        from .queue import ScheduleLease

        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM schedule_leases ORDER BY scope").fetchall()
        return [ScheduleLease._from_row(r) for r in rows]

    # -- external producer resource reservations ----------------------------

    def acquire_resource_reservation(
        self,
        key: str,
        owner: str,
        *,
        ttl: float,
        token: str | None = None,
        now: float | None = None,
    ) -> tuple[ResourceReservation, bool]:
        """Atomically elect one owner for an external logical resource.

        An unbound reservation expires so another producer can recover after a
        crash before task creation. Once bound to a task, it remains owned until
        explicit terminal reconciliation releases it.
        """
        from .queue import ResourceReservation, TaskError

        if not key or not owner:
            raise TaskError("resource reservation key and owner are required")
        if ttl <= 0:
            raise TaskError("resource reservation ttl must be positive")
        ts = self._now(now)
        expires_at = ts + ttl
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                token = secrets.token_urlsafe(24)
                conn.execute(
                    "INSERT INTO resource_reservations "
                    "(key, owner, token, task_id, acquired_at, updated_at, expires_at) "
                    "VALUES (?, ?, ?, NULL, ?, ?, ?)",
                    (key, owner, token, ts, ts, expires_at),
                )
                granted = True
            elif (
                token is not None
                and row["owner"] == owner
                and secrets.compare_digest(row["token"], token)
            ):
                if row["task_id"] is None:
                    conn.execute(
                        "UPDATE resource_reservations "
                        "SET updated_at = ?, expires_at = ? WHERE key = ?",
                        (ts, expires_at, key),
                    )
                granted = True
            elif row["task_id"] is None and (
                row["expires_at"] is not None and row["expires_at"] <= ts
            ):
                token = secrets.token_urlsafe(24)
                conn.execute(
                    "UPDATE resource_reservations SET owner = ?, token = ?, task_id = NULL, "
                    "acquired_at = ?, updated_at = ?, expires_at = ? WHERE key = ?",
                    (owner, token, ts, ts, expires_at, key),
                )
                granted = True
            else:
                granted = False
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return ResourceReservation._from_row(row), granted

    def bind_resource_reservation(
        self,
        key: str,
        owner: str,
        token: str,
        task_id: str,
        *,
        now: float | None = None,
    ) -> ResourceReservation:
        """Bind an owned reservation to its created task."""
        from .queue import ResourceReservation, TaskError

        if not token or not task_id:
            raise TaskError("resource reservation token and task_id are required")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such resource reservation: {key}")
            if row["owner"] != owner or not secrets.compare_digest(row["token"], token):
                conn.execute("COMMIT")
                raise TaskError(f"resource reservation {key!r} identity does not match")
            if row["task_id"] not in (None, task_id):
                conn.execute("COMMIT")
                raise TaskError(
                    f"resource reservation {key!r} is already bound to {row['task_id']!r}"
                )
            conn.execute(
                "UPDATE resource_reservations "
                "SET task_id = ?, updated_at = ?, expires_at = NULL WHERE key = ?",
                (task_id, ts, key),
            )
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return ResourceReservation._from_row(row)

    def release_resource_reservation(self, key: str, owner: str, token: str) -> bool:
        """Release only the caller's own resource reservation.

        A non-owner receives ``False``; the current owner's reservation is
        never modified.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, token FROM resource_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            if row["owner"] != owner or not secrets.compare_digest(row["token"], token):
                conn.execute("COMMIT")
                return False
            conn.execute("DELETE FROM resource_reservations WHERE key = ?", (key,))
            conn.execute("COMMIT")
        return True

    def list_resource_reservations(
        self,
        *,
        owner_prefix: str | None = None,
        task_id: str | None = None,
    ) -> list[ResourceReservation]:
        from .queue import ResourceReservation

        clauses: list[str] = []
        params: list[object] = []
        if owner_prefix is not None:
            clauses.append("substr(owner, 1, ?) = ?")
            params.extend((len(owner_prefix), owner_prefix))
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_reservations" + where + " ORDER BY key",
                params,
            ).fetchall()
        return [ResourceReservation._from_row(row) for row in rows]
