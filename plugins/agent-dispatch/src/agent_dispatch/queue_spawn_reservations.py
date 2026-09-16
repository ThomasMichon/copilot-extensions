"""``TaskQueue`` mixin: atomic spawn-reservation lifecycle (the
spawn-at-most-once safety model over ``spawn_reservations``).

Extracted from :mod:`agent_dispatch.queue` (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423), covering
reservation acquisition (``reserve_spawn``), rearm/retire/defer/fail
transitions, and conclusion claiming. Verified self-contained the same way
as every prior slice: an AST free-name walk found only module-level
helpers themselves used exclusively by this cluster (``spawn_key``,
``_conclusion_payload``, ``_validate_conclusion_claim``,
``_newer_worktree_reservation`` -- all move here too), and no test
monkeypatches any of these methods by name. The three simple read-only
lookups (``get_reservation``, ``latest_reservation``,
``list_reservations``) stayed behind in ``queue.py`` itself, both because
``list_reservations`` calls ``self._canonical_repo`` (a ``TaskQueue``
helper genuinely defined there) and to keep this module comfortably under
the 1,000-line cap.

``SpawnReservation``/``TaskError``/``SpawnState``/``Status`` live in the
dependency-free :mod:`agent_dispatch.queue_records`, imported here as
ordinary top-level names -- no circular import, and
``typing.get_type_hints()`` resolves cleanly on every method (applying the
``queue_schedule_registry.py`` review lesson from the start).

:class:`SpawnReservationMixin` is composed into
:class:`agent_dispatch.queue.TaskQueue`; it relies on ``self._connect()``
and ``self._now()`` from that class and is not usable standalone.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from .queue_records import SpawnReservation, SpawnState, Status, TaskError


def spawn_key(task_id: str, attempt: int) -> str:
    """The canonical reservation key for a (task, attempt) spawn."""
    return f"dispatch-task:{task_id}:{attempt}"


def _conclusion_payload(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _validate_conclusion_claim(
    stored_token: object,
    stored_expires_at: object,
    supplied_token: str | None,
    *,
    now: float,
    required: bool = False,
) -> None:
    if required and not stored_token:
        raise TaskError("cleanup claim token is required")
    if isinstance(stored_token, str) and stored_token:
        if supplied_token is None:
            raise TaskError("cleanup claim token is required")
        if supplied_token != stored_token:
            raise TaskError("cleanup claim changed")
        try:
            claim_expires_at = float(stored_expires_at or 0)
        except (TypeError, ValueError):
            claim_expires_at = 0.0
        if claim_expires_at <= now:
            raise TaskError("cleanup claim expired")
    elif supplied_token is not None:
        raise TaskError("cleanup claim changed")


def _newer_worktree_reservation(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    key: str,
) -> sqlite3.Row | None:
    if not row["worktree"]:
        return None
    return conn.execute(
        "SELECT key FROM spawn_reservations "
        "WHERE key <> ? AND (worktree = ? OR inherited_worktree = ?) "
        "AND reserved_at > ? ORDER BY reserved_at DESC LIMIT 1",
        (
            key,
            row["worktree"],
            row["worktree"],
            row["reserved_at"],
        ),
    ).fetchone()


class SpawnReservationMixin:
    """Spawn-reservation lifecycle methods for :class:`TaskQueue`."""

    # -- spawn reservations --------------------------------------------------

    def reserve_spawn(
        self, task_id: str, *, reserved_by: str | None = None, now: float | None = None
    ) -> tuple[SpawnReservation, bool]:
        """Atomically reserve the right to spawn an embody worker for ``task_id``.

        This is the primitive that makes "queued task -> exactly one host embody
        session" durable and idempotent. It is **distinct from the execution
        claim**: the claim is taken later by the embodied worker under its own
        worktree identity; this reservation is taken by the *spawner* (a
        ``create --spawn`` CLI, or the supervisor loop) *before* launching
        embody, so a crash / re-poll / lease-expiry between observing a
        spawn-eligible task and actually spawning it can never double-spawn.

        Semantics (all under one write lock):

        * If an **active** reservation
          (``reserving``/``spawned``/``cold``/``releasing``) already exists for
          the task, return it with ``False`` -- the task is already being
          spawned or its prior allocation is still being released; the caller
          must **not** spawn.
        * Otherwise mint a fresh reservation. ``attempt`` is ``max(prior
          attempts) + 1`` (``1`` for the first), keyed
          ``dispatch-task:<task_id>:<attempt>``, in state ``reserving``. Return
          it with ``True`` -- the caller owns this spawn.

        A prior ``failed``/``settled`` reservation therefore does not block a
        retry: the next attempt gets a fresh key.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt ASC",
                (task_id,),
            ).fetchall()
            if task.exclusive_key is not None:
                active = conn.execute(
                    "SELECT * FROM spawn_reservations "
                    "WHERE exclusive_key = ? AND state IN (?, ?, ?, ?) "
                    "ORDER BY reserved_at ASC LIMIT 1",
                    (
                        task.exclusive_key,
                        SpawnState.RESERVING,
                        SpawnState.SPAWNED,
                        SpawnState.COLD,
                        SpawnState.RELEASING,
                    ),
                ).fetchone()
                if active is not None:
                    conn.execute("COMMIT")
                    return SpawnReservation._from_row(active), False
            else:
                for row in rows:
                    if row["state"] in SpawnState.ACTIVE:
                        conn.execute("COMMIT")
                        return SpawnReservation._from_row(row), False
            if task.status != Status.QUEUED or task.owner is not None:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is {task.status!r} with owner "
                    f"{task.owner!r}; spawn reservation requires queued and unowned"
                )
            attempt = (max(r["attempt"] for r in rows) + 1) if rows else 1
            key = spawn_key(task_id, attempt)
            carried_worktree = task.affinity.get("worktree")
            worktree_ownership = "targeted" if carried_worktree else None
            carried_session = None
            if task.exclusive_key is not None:
                prior = conn.execute(
                    "SELECT session_handle, worktree FROM spawn_reservations "
                    "WHERE exclusive_key = ? AND worktree IS NOT NULL "
                    "ORDER BY reserved_at DESC LIMIT 1",
                    (task.exclusive_key,),
                ).fetchone()
                if prior is not None:
                    carried_worktree = prior["worktree"]
                    carried_session = prior["session_handle"]
                    if carried_session is not None:
                        # A later failed attempt may have copied this handle
                        # before the retiring settlement was understood.
                        retired = conn.execute(
                            "SELECT 1 FROM spawn_reservations "
                            "WHERE exclusive_key = ? AND session_handle = ? "
                            "AND release_requested = 1 AND state IN (?, ?) LIMIT 1",
                            (
                                task.exclusive_key,
                                carried_session,
                                SpawnState.SETTLED,
                                SpawnState.FAILED,
                            ),
                        ).fetchone()
                        if retired is not None:
                            carried_session = None
                    worktree_ownership = "reused"
            if carried_worktree is not None:
                cleanup_claims = conn.execute(
                    "SELECT key FROM spawn_reservations "
                    "WHERE worktree = ? AND state IN (?, ?) "
                    "AND conclusion_state = ?",
                    (
                        carried_worktree,
                        SpawnState.FAILED,
                        SpawnState.SETTLED,
                        "pending",
                    ),
                ).fetchall()
                for cleanup_claim in cleanup_claims:
                    carried_worktree = None
                    carried_session = None
                    worktree_ownership = None
                    break
            conn.execute(
                "INSERT INTO spawn_reservations "
                "(key, task_id, exclusive_key, attempt, state, reserved_by, "
                "session_handle, worktree, inherited_worktree, "
                "worktree_ownership, reserved_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    task_id,
                    task.exclusive_key,
                    attempt,
                    SpawnState.RESERVING,
                    reserved_by,
                    carried_session,
                    carried_worktree,
                    carried_worktree,
                    worktree_ownership,
                    ts,
                    ts,
                ),
            )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row), True

    def rearm_spawn(
        self,
        task_id: str,
        *,
        permitted: bool = False,
        reason: str | None = None,
        min_failures: int = 3,
        now: float | None = None,
    ) -> dict[str, object]:
        """Atomically retire failed spawn attempts so one fresh retry is eligible.

        The task must still be queued and unowned, no active reservation may
        exist, and at least ``min_failures`` failed attempts must be present.
        All checks and the failed->rearmed transition share one
        ``BEGIN IMMEDIATE`` transaction with task claims and spawn reservations.
        """
        if not permitted:
            raise TaskError("rearming spawn reservations requires explicit permission")
        reason = (reason or "").strip()
        if not reason:
            raise TaskError("rearming spawn reservations requires a non-empty reason")
        if min_failures < 3:
            raise TaskError("min_failures must be at least 3")

        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.QUEUED or task.owner is not None:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is {task.status!r} with owner "
                    f"{task.owner!r}; rearm requires queued and unowned"
                )
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt ASC",
                (task_id,),
            ).fetchall()
            active = [row["key"] for row in rows if row["state"] in SpawnState.ACTIVE]
            if active:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has active spawn reservation(s): {', '.join(active)}"
                )
            failed = [row for row in rows if row["state"] == SpawnState.FAILED]
            pending_cleanup = [
                row["key"] for row in failed if row["conclusion_state"] == "pending"
            ]
            if pending_cleanup:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has pending spawn cleanup: {', '.join(pending_cleanup)}"
                )
            if len(failed) < min_failures:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has {len(failed)} failed spawn reservation(s); "
                    f"at least {min_failures} required"
                )

            keys: list[str] = []
            for row in failed:
                keys.append(row["key"])
                prior = (row["detail"] or "").strip()
                detail = f"{prior}\nrearmed: {reason}".strip()
                conn.execute(
                    "UPDATE spawn_reservations "
                    "SET state = ?, updated_at = ?, detail = ? WHERE key = ?",
                    (SpawnState.REARMED, ts, detail, row["key"]),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=Status.QUEUED,
                to_status=Status.QUEUED,
                worker="operator",
                note=f"spawn reservations rearmed: {reason}",
            )
            conn.execute("COMMIT")
        return {
            "task_id": task_id,
            "rearmed": len(keys),
            "reservation_keys": keys,
            "reason": reason,
            "next_attempt": max(row["attempt"] for row in rows) + 1,
        }

    def _update_reservation(
        self,
        key: str,
        *,
        to_state: str,
        allowed_from: frozenset[str],
        now: float | None = None,
        session_handle: str | None = None,
        worktree: str | None = None,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
    ) -> SpawnReservation:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in allowed_from:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not one of "
                    f"{sorted(allowed_from)} (cannot -> {to_state!r})"
                )
            if to_state == SpawnState.DEFERRED and row["release_requested"]:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} has pending release (cannot defer)")
            if (
                conclusion_state is not None
                or conclusion_detail is not None
                or claim_token is not None
            ):
                _validate_conclusion_claim(
                    row["cleanup_claim_token"],
                    row["cleanup_claim_expires_at"],
                    claim_token,
                    now=ts,
                    required=(
                        row["state"]
                        in {
                            SpawnState.FAILED,
                            SpawnState.SETTLED,
                        }
                        and row["conclusion_state"] == "pending"
                    ),
                )
                if claim_token is not None:
                    newer = _newer_worktree_reservation(conn, row, key)
                    if newer is not None:
                        conn.execute("COMMIT")
                        raise TaskError(f"worktree carried by newer reservation {newer['key']}")
            if worktree is not None:
                cleanup_claims = conn.execute(
                    "SELECT key FROM spawn_reservations "
                    "WHERE key <> ? AND worktree = ? AND state IN (?, ?) "
                    "AND conclusion_state = ?",
                    (
                        key,
                        worktree,
                        SpawnState.FAILED,
                        SpawnState.SETTLED,
                        "pending",
                    ),
                ).fetchall()
                for cleanup_claim in cleanup_claims:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"worktree {worktree!r} has in-flight cleanup {cleanup_claim['key']}"
                    )
            conn.execute(
                "UPDATE spawn_reservations SET state = ?, updated_at = ?, "
                "session_handle = CASE WHEN ? IS NOT NULL THEN ? ELSE session_handle END, "
                "worktree = CASE WHEN ? IS NOT NULL THEN ? ELSE worktree END, "
                "inherited_worktree = CASE "
                "WHEN inherited_worktree IS NOT NULL THEN inherited_worktree "
                "WHEN worktree_ownership = 'reused' THEN worktree "
                "ELSE inherited_worktree END, "
                "detail = COALESCE(?, detail), "
                "conclusion_state = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_state END, "
                "conclusion_detail = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_detail END, "
                "cleanup_claim_expires_at = CASE WHEN ? IS NOT NULL THEN 0 "
                "ELSE cleanup_claim_expires_at END WHERE key = ?",
                (
                    to_state,
                    ts,
                    session_handle,
                    session_handle,
                    worktree,
                    worktree,
                    detail,
                    conclusion_state,
                    conclusion_state,
                    conclusion_detail,
                    conclusion_detail,
                    claim_token,
                    key,
                ),
            )
            if to_state in SpawnState.RELEASABLE and row["state"] in SpawnState.ACTIVE:
                latest = conn.execute(
                    "SELECT key FROM spawn_reservations WHERE task_id = ? "
                    "ORDER BY attempt DESC LIMIT 1",
                    (row["task_id"],),
                ).fetchone()
                if latest is not None and latest["key"] == key:
                    conn.execute(
                        "UPDATE tasks SET activity = NULL, activity_updated_at = ? WHERE id = ?",
                        (ts, row["task_id"]),
                    )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)

    def record_spawn(
        self,
        key: str,
        *,
        session_handle: str | None = None,
        worktree: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``spawned`` and record its embody session handle.

        Called right after a successful ``agent-worktrees embody`` launch. The
        handle is what lets a supervisor restart reconcile (join the reservation
        to the live session) instead of re-spawning.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.SPAWNED,
            allowed_from=frozenset({SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.COLD}),
            session_handle=session_handle,
            worktree=worktree,
            now=now,
        )

    def record_spawn_worktree(
        self,
        key: str,
        worktree: str,
        *,
        ownership: str = "unknown",
        creating_host: str | None = None,
        driver: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Record a reusable worktree while the reservation is still reserving.

        Replacing a carried worktree clears its carried session handle: a
        confirmed-missing worktree cannot safely retain a session binding from
        the vanished checkout.
        """
        worktree = worktree.strip()
        if not worktree:
            raise TaskError("spawn worktree must be non-empty")
        if ownership not in {"created", "targeted", "reused", "unknown"}:
            raise TaskError(f"invalid spawn worktree ownership: {ownership!r}")
        if ownership != "created":
            creating_host = None
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, worktree, worktree_ownership, inherited_worktree "
                "FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] != SpawnState.RESERVING:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not {SpawnState.RESERVING!r}"
                )
            cleanup_claims = conn.execute(
                "SELECT key FROM spawn_reservations "
                "WHERE key <> ? AND worktree = ? AND state IN (?, ?) "
                "AND conclusion_state = ?",
                (
                    key,
                    worktree,
                    SpawnState.FAILED,
                    SpawnState.SETTLED,
                    "pending",
                ),
            ).fetchall()
            for cleanup_claim in cleanup_claims:
                conn.execute("COMMIT")
                raise TaskError(
                    f"worktree {worktree!r} has in-flight cleanup {cleanup_claim['key']}"
                )
            conn.execute(
                "UPDATE spawn_reservations SET "
                "session_handle = CASE "
                "WHEN worktree IS NULL OR worktree <> ? THEN NULL "
                "ELSE session_handle END, "
                "inherited_worktree = CASE "
                "WHEN inherited_worktree IS NOT NULL THEN inherited_worktree "
                "WHEN worktree_ownership = 'reused' THEN worktree "
                "ELSE inherited_worktree END, "
                "worktree = ?, worktree_ownership = ?, creating_host = ?, "
                "driver = ?, updated_at = ? WHERE key = ?",
                (
                    worktree,
                    worktree,
                    ownership,
                    creating_host,
                    driver,
                    ts,
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def record_cold(self, key: str, *, now: float | None = None) -> SpawnReservation:
        """Mark a spawned headless body intentionally stopped and dormant."""
        return self._update_reservation(
            key,
            to_state=SpawnState.COLD,
            allowed_from=frozenset({SpawnState.SPAWNED, SpawnState.COLD}),
            now=now,
        )

    def fail_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``failed`` (spawn failed or lost), releasing the
        task so a fresh attempt may be reserved.

        Repeating the call on an already-failed row is an idempotent metadata
        update, allowing allocation cleanup to remain inspectable after body
        release.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.FAILED,
            allowed_from=(SpawnState.ACTIVE - frozenset({SpawnState.RELEASING}))
            | frozenset({SpawnState.FAILED}),
            detail=detail,
            conclusion_state=conclusion_state,
            conclusion_detail=conclusion_detail,
            claim_token=claim_token,
            now=now,
        )

    def defer_spawn(
        self, key: str, *, detail: str | None = None, now: float | None = None
    ) -> SpawnReservation:
        """Mark a reservation ``deferred``: the spawn declined, not failed,
        because a carried session (same exclusive key) was confirmed still
        live/busy. Releases the task so a fresh attempt may be reserved
        immediately, but -- unlike :meth:`fail_spawn` -- never counts toward
        dead-lettering, since no attempt actually failed."""
        return self._update_reservation(
            key,
            to_state=SpawnState.DEFERRED,
            allowed_from=(SpawnState.ACTIVE - frozenset({SpawnState.RELEASING})),
            detail=detail,
            now=now,
        )

    def retire_spawn(
        self,
        key: str,
        *,
        exact_absence: bool,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Atomically retire a RELEASING reservation on exact absence proof."""
        if not exact_absence:
            raise TaskError("spawn retirement requires exact absence proof")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] != SpawnState.RELEASING:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} is {row['state']!r}, not releasing")
            to_state = (
                SpawnState.SETTLED
                if row["release_disposition"] == "settled"
                else SpawnState.FAILED
            )
            conn.execute(
                "UPDATE spawn_reservations SET state = ?, updated_at = ?, "
                "detail = COALESCE(?, detail), "
                "conclusion_state = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_state END, "
                "conclusion_detail = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_detail END WHERE key = ?",
                (
                    to_state,
                    ts,
                    detail,
                    conclusion_state,
                    conclusion_state,
                    conclusion_detail,
                    conclusion_detail,
                    key,
                ),
            )
            latest = conn.execute(
                "SELECT key FROM spawn_reservations WHERE task_id = ? "
                "ORDER BY attempt DESC LIMIT 1",
                (row["task_id"],),
            ).fetchone()
            if latest is not None and latest["key"] == key:
                conn.execute(
                    "UPDATE tasks SET activity = NULL, activity_updated_at = ? WHERE id = ?",
                    (ts, row["task_id"]),
                )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
        session_handle: str | None = None,
        worktree: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Fence an attempt and atomically retain any failed body identity."""
        if disposition not in {"failed", "settled"}:
            raise TaskError(f"invalid spawn release disposition: {disposition!r}")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in SpawnState.ACTIVE:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not active (cannot request release)"
                )
            if worktree and row["worktree"] and row["worktree"] != worktree:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} worktree is {row['worktree']!r}, "
                    f"not {worktree!r}"
                )
            if row["state"] == SpawnState.RELEASING:
                release_disposition = (
                    "failed"
                    if "failed" in {row["release_disposition"], disposition}
                    else row["release_disposition"] or disposition
                )
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested = 1, "
                    "release_disposition = ?, detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "updated_at = ? WHERE key = ?",
                    (
                        release_disposition,
                        detail,
                        session_handle,
                        worktree,
                        ts,
                        key,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE spawn_reservations SET state = ?, "
                    "release_requested = 1, release_disposition = ?, "
                    "detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "conclusion_state = COALESCE(conclusion_state, ?), "
                    "updated_at = ? WHERE key = ?",
                    (
                        SpawnState.RELEASING,
                        disposition,
                        detail,
                        session_handle,
                        worktree,
                        "pending",
                        ts,
                        key,
                    ),
                )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)

    def settle_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``settled`` (its task reached a terminal outcome).

        Repeating the call on an already-settled row is an idempotent detail
        update. Once exact body absence is proven, allocation cleanup may remain
        ``pending`` or ``held`` here as inspectable conclusion metadata without
        retaining an active spawn fence.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.SETTLED,
            allowed_from=(SpawnState.ACTIVE - frozenset({SpawnState.RELEASING}))
            | frozenset({SpawnState.SETTLED}),
            detail=detail,
            conclusion_state=conclusion_state,
            conclusion_detail=conclusion_detail,
            claim_token=claim_token,
            now=now,
        )

    def record_spawn_conclusion(
        self,
        key: str,
        *,
        conclusion_state: str,
        conclusion_detail: str,
        detail: str | None = None,
        claim_token: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Persist conclusion progress on an active or retired reservation."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, conclusion_state, conclusion_detail, "
                "cleanup_claim_token, cleanup_claim_expires_at, "
                "worktree, reserved_at "
                "FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            mutable_states = SpawnState.ACTIVE | frozenset({SpawnState.FAILED, SpawnState.SETTLED})
            if row["state"] not in mutable_states:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} is {row['state']!r}, not mutable")
            _validate_conclusion_claim(
                row["cleanup_claim_token"],
                row["cleanup_claim_expires_at"],
                claim_token,
                now=ts,
                required=(
                    row["state"]
                    in {
                        SpawnState.FAILED,
                        SpawnState.SETTLED,
                    }
                    and row["conclusion_state"] == "pending"
                ),
            )
            if claim_token is not None:
                newer = _newer_worktree_reservation(conn, row, key)
                if newer is not None:
                    conn.execute("COMMIT")
                    raise TaskError(f"worktree carried by newer reservation {newer['key']}")
            conn.execute(
                "UPDATE spawn_reservations SET updated_at = ?, "
                "detail = COALESCE(?, detail), conclusion_state = ?, "
                "conclusion_detail = ?, "
                "cleanup_claim_expires_at = CASE WHEN ? IS NOT NULL THEN 0 "
                "ELSE cleanup_claim_expires_at END WHERE key = ?",
                (
                    ts,
                    detail,
                    conclusion_state,
                    conclusion_detail,
                    claim_token,
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def claim_spawn_conclusion_retry(
        self,
        key: str,
        *,
        claim_seconds: float = 300.0,
        now: float | None = None,
    ) -> tuple[SpawnReservation, bool, str | None]:
        """Claim a retired cleanup retry without racing worktree reuse."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if (
                row["state"] not in {SpawnState.FAILED, SpawnState.SETTLED}
                or row["conclusion_state"] != "pending"
            ):
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} has no retired pending cleanup")
            try:
                current_expiry = float(row["cleanup_claim_expires_at"] or 0)
            except (TypeError, ValueError):
                current_expiry = 0.0
            if row["cleanup_claim_token"] and current_expiry > ts:
                conn.execute("COMMIT")
                return SpawnReservation._from_row(row), False, None
            blocker = None
            if row["worktree"]:
                blocker = conn.execute(
                    "SELECT key FROM spawn_reservations "
                    "WHERE key <> ? AND (worktree = ? OR inherited_worktree = ?) "
                    "AND reserved_at > ? "
                    "ORDER BY reserved_at DESC LIMIT 1",
                    (
                        key,
                        row["worktree"],
                        row["worktree"],
                        row["reserved_at"],
                    ),
                ).fetchone()
            claim_token = None
            if blocker is not None:
                payload: dict[str, object] = {
                    "action": "preserved",
                    "reason": "worktree-carried-by-newer-reservation",
                    "prior": row["conclusion_detail"],
                }
                conclusion_state = "held"
            else:
                claim_token = uuid.uuid4().hex
                payload = {
                    **_conclusion_payload(row["conclusion_detail"]),
                    "action": "pending",
                    "reason": "cleanup-retry-claimed",
                    "claim_token": claim_token,
                    "claim_expires_at": ts + max(1.0, claim_seconds),
                }
                conclusion_state = "pending"
            if blocker is not None:
                payload["blocking_reservation_key"] = blocker["key"]
            conn.execute(
                "UPDATE spawn_reservations SET updated_at = ?, "
                "conclusion_state = ?, conclusion_detail = ?, "
                "cleanup_claim_token = ?, cleanup_claim_expires_at = ? "
                "WHERE key = ?",
                (
                    ts,
                    conclusion_state,
                    json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    claim_token,
                    (ts + max(1.0, claim_seconds) if claim_token is not None else None),
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated), blocker is None, claim_token

    def validate_spawn_conclusion_claim(
        self,
        key: str,
        claim_token: str,
        *,
        now: float | None = None,
    ) -> SpawnReservation:
        """Revalidate a cleanup lease and worktree fence before side effects."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            _validate_conclusion_claim(
                row["cleanup_claim_token"],
                row["cleanup_claim_expires_at"],
                claim_token,
                now=ts,
            )
            newer = _newer_worktree_reservation(conn, row, key)
            if newer is not None:
                conn.execute("COMMIT")
                raise TaskError(f"worktree carried by newer reservation {newer['key']}")
            updated = SpawnReservation._from_row(row)
            conn.execute("COMMIT")
        return updated
