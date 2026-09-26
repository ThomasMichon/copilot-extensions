"""``TaskQueue`` mixin: card/steer, wake-outbox, and pin-portability methods.

Extracted from :mod:`agent_dispatch.queue` as the next coherent responsibility
band after the earlier schedule / routing / spawn / producer-fence splits:
operator interaction state (cards, steer drafts/answers, wake delivery, and the
small pin-portability ``detach`` helper) is distinct from task creation,
claiming, and lifecycle transitions.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned primitives such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, ``self._now()``,
``self._enqueue_wake()``'s transaction context, and the wake/owned-transition
notifiers. It is not usable standalone.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from .queue_common import (
    DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    Task,
    WakeOperation,
    _check_expected_status,
)
from .queue_records import SpawnState, Status, TaskError
from .registrations import RegistrationKind, RegistrationStatus


class QueueSteeringMixin:
    """Card/steer, wake-outbox, and detach helpers for :class:`TaskQueue`."""

    @staticmethod
    def _has_headless_reservation(conn: sqlite3.Connection, task_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM spawn_reservations"
            " WHERE task_id = ? AND state IN (?, ?) AND"
            " (session_handle LIKE 'local-body:%' OR"
            " session_handle LIKE 'fleet-body:%')"
            " ORDER BY attempt DESC LIMIT 1",
            (task_id, SpawnState.SPAWNED, SpawnState.COLD),
        ).fetchone()
        return row is not None

    @staticmethod
    def _has_cold_headless_reservation(conn: sqlite3.Connection, task_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM spawn_reservations"
            " WHERE task_id = ? AND state = ? AND"
            " (session_handle LIKE 'local-body:%' OR"
            " session_handle LIKE 'fleet-body:%')"
            " LIMIT 1",
            (task_id, SpawnState.COLD),
        ).fetchone()
        return row is not None

    def _steering_disallowed_labels(self, *, target_machine: str | None) -> set[str]:
        """Union of ``steering_disallowed_labels`` from every active, in-scope
        registration -- the coordinator-side denylist a ``card
        set --request-input`` is checked against (see :meth:`set_card`).

        A registration is in scope for ``target_machine`` when its own
        ``machine`` is unset (facility-wide) or matches exactly. Only the two
        kinds that carry a ``labels``/``steering_disallowed_labels`` pair
        (mirroring :data:`Supervisor.idle_nudge_exempt_labels`'s scope) are
        consulted.
        """
        blocked: set[str] = set()
        for kind in (RegistrationKind.SUPERVISED_LANE, RegistrationKind.EVALUATOR):
            for reg in self.list_registrations(kind=kind, include_paused=False):
                if reg.status != RegistrationStatus.ACTIVE:
                    continue
                if reg.machine is not None and reg.machine != target_machine:
                    continue
                labels = reg.spec.get("steering_disallowed_labels")
                if labels:
                    blocked.update(labels)
        return blocked

    def set_card(
        self,
        task_id: str,
        worker_id: str,
        *,
        card: dict,
        now: float | None = None,
    ) -> Task:
        """Attach a **card** to a held task the worker owns.

        A ``request_input`` card (the capability that suspends the task in
        ``awaiting_steer`` until a human answers) is refused with a
        :class:`TaskError` when the task carries a label any active
        registration has declared via ``steering_disallowed_labels`` --
        e.g. an evaluator-owned recipe (Intelligence Dampener's reviewer),
        a batch log writer, or an Adjudication Board worker, none of which
        have a human to hand a card to. The **default is permissive**: a
        task is free to post a steering card unless a registration
        explicitly names one of its labels (see aperture-labs#7589/#7585 and
        copilot-extensions#3731 for the confirmed live incident this closes).
        A card with no ``request_input`` (a plain status note) is never
        gated -- it does not block the task's own resume path.
        """
        ts = self._now(now)
        card = {**card, "ts": ts}
        payload = json.dumps(card, separators=(",", ":"))
        awaiting = 1 if card.get("request_input") else 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot set a card on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if awaiting:
                blocked = self._steering_disallowed_labels(target_machine=task.target_machine)
                stray = blocked & set(task.labels)
                if stray:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"task {task_id!r} carries label(s) {sorted(stray)}, which a "
                        "registration's 'steering_disallowed_labels' forbids from "
                        "posting a request-input steering card -- this task type "
                        "owns its own resume path and has no human to hand a card to"
                    )
            to_status = Status.SUSPENDED if awaiting else task.status
            lease_expires_at = None if awaiting else ts + self.lease_seconds
            conn.execute(
                "UPDATE tasks SET card = ?, awaiting_steer = ?, status = ?,"
                " lease_expires_at = ?, last_liveness = NULL,"
                " activity = NULL, activity_updated_at = ?,"
                " last_seen_at = ?, updated_at = ? WHERE id = ?",
                (
                    payload,
                    awaiting,
                    to_status,
                    lease_expires_at,
                    ts,
                    ts,
                    ts,
                    task_id,
                ),
            )
            note = "card posted (awaiting steer)" if awaiting else "card posted"
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=to_status,
                worker=worker_id,
                note=note,
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def submit_steer(
        self,
        task_id: str,
        *,
        fields: dict,
        sender: str | None = None,
        wake_requested: bool = False,
        wake_message: str | None = None,
        expected_status: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Submit an operator's answer (a **steer**) to a task's card."""
        ts = self._now(now)
        payload = json.dumps(fields, separators=(",", ":"))
        wake_enqueued = False
        notify_owned_transition = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            try:
                _check_expected_status(task, expected_status)
            except TaskError:
                conn.execute("COMMIT")
                raise
            if task.status in Status.CONCLUDED:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot steer a {task.status!r} task"
                    + (
                        " (use reopen_completed to re-queue it with steering)"
                        if task.status == Status.COMPLETED
                        else ""
                    )
                )
            conn.execute(
                "INSERT INTO task_steer (task_id, ts, fields, sender) VALUES (?, ?, ?, ?)",
                (task_id, ts, payload, sender),
            )
            resumed = task.status == Status.SUSPENDED
            cold_headless = bool(resumed and self._has_headless_reservation(conn, task_id))
            notify_owned_transition = resumed
            if cold_headless:
                conn.execute(
                    "UPDATE tasks SET awaiting_steer = 0, resume_requested = 1,"
                    " card_draft = NULL, updated_at = ?"
                    " WHERE id = ? AND status = ?",
                    (ts, task_id, Status.SUSPENDED),
                )
            elif resumed:
                conn.execute(
                    "UPDATE tasks SET status = ?, awaiting_steer = 0,"
                    " resume_requested = 0, lease_expires_at = ?,"
                    " last_seen_at = ?, last_liveness = NULL,"
                    " card_draft = NULL,"
                    " updated_at = ? WHERE id = ? AND status = ?",
                    (
                        Status.STARTED,
                        ts + self.lease_seconds,
                        ts,
                        ts,
                        task_id,
                        Status.SUSPENDED,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET awaiting_steer = 0, card_draft = NULL,"
                    " updated_at = ? WHERE id = ?",
                    (ts, task_id),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=(
                    Status.SUSPENDED
                    if cold_headless
                    else Status.STARTED
                    if resumed
                    else task.status
                ),
                worker=sender,
                note=(
                    f"steer submitted{f' by {sender}' if sender else ''}"
                    f"{'; resume requested for cold body' if cold_headless else ''}"
                    f"{'; resumed' if resumed and not cold_headless else ''}"
                ),
            )
            result = self._fetch(conn, task_id)
            if (
                wake_requested
                and not cold_headless
                and result is not None
                and result.owner
                and result.owner_session_id is not None
            ):
                self._enqueue_wake(
                    conn,
                    result,
                    message=wake_message,
                    ts=ts,
                )
                wake_enqueued = True
                result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        if wake_enqueued:
            self._notify_wake()
        if notify_owned_transition:
            self._notify_owned_transition()
        return result  # type: ignore[return-value]

    def save_card_draft(
        self,
        task_id: str,
        *,
        fields: dict,
        now: float | None = None,
    ) -> Task:
        """Persist an operator's **not-yet-submitted** draft answer."""
        ts = self._now(now)
        draft = {"fields": fields, "ts": ts}
        payload = json.dumps(draft, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status in Status.CONCLUDED:
                conn.execute("COMMIT")
                raise TaskError(f"cannot save a draft on a {task.status!r} task")
            conn.execute(
                "UPDATE tasks SET card_draft = ?, updated_at = ? WHERE id = ?",
                (payload, ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def clear_card_draft(self, task_id: str, *, now: float | None = None) -> Task:
        """Clear a task's saved draft."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            conn.execute(
                "UPDATE tasks SET card_draft = NULL, updated_at = ? WHERE id = ?",
                (ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def take_steer(
        self,
        task_id: str,
        worker_id: str,
        *,
        all_pending: bool = False,
        now: float | None = None,
    ) -> dict | list[dict] | None:
        """Consume pending steering for a held task the worker owns."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot take a steer on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            rows = conn.execute(
                "SELECT id, ts, fields, sender FROM task_steer "
                "WHERE task_id = ? AND taken = 0 ORDER BY id ASC"
                + ("" if all_pending else " LIMIT 1"),
                (task_id,),
            ).fetchall()
            if not rows:
                conn.execute(
                    "UPDATE tasks SET lease_expires_at = ?, last_seen_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (ts + self.lease_seconds, ts, ts, task_id),
                )
                conn.execute("COMMIT")
                return [] if all_pending else None
            conn.executemany(
                "UPDATE task_steer SET taken = 1, taken_at = ? WHERE id = ?",
                [(ts, row["id"]) for row in rows],
            )
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, last_seen_at = ?,"
                " updated_at = ? WHERE id = ?",
                (ts + self.lease_seconds, ts, ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=(f"{len(rows)} steers taken" if all_pending else "steer taken"),
            )
            conn.execute("COMMIT")
        result = [
            {
                "id": row["id"],
                "ts": row["ts"],
                "fields": json.loads(row["fields"] or "{}"),
                "sender": row["sender"],
            }
            for row in rows
        ]
        return result if all_pending else result[0]

    def steer_log(self, task_id: str) -> list[dict]:
        """The full steer inbox for a task (oldest first), for inspection."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, fields, sender, taken, taken_at FROM task_steer "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "fields": json.loads(r["fields"] or "{}"),
                "sender": r["sender"],
                "taken": bool(r["taken"]),
                "taken_at": r["taken_at"],
            }
            for r in rows
        ]

    def list_wakes(self, task_id: str) -> list[WakeOperation]:
        """List a task's durable wake operations, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM wake_outbox WHERE task_id = ? ORDER BY wake_seq ASC",
                (task_id,),
            ).fetchall()
        return [WakeOperation._from_row(row) for row in rows]

    @staticmethod
    def _wake_is_current(task: Task | None, wake: WakeOperation) -> bool:
        return bool(
            task is not None
            and task.status == Status.STARTED
            and task.owner == wake.owner
            and wake.owner_session_id is not None
            and task.owner_session_id == wake.owner_session_id
            and task.generation == wake.generation
            and task.wake_operation_id == wake.id
        )

    def recover_inflight_wakes(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    ) -> int:
        """Return only expired ``delivering`` rows to pending."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM wake_outbox WHERE status = 'delivering'"
                " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                (max(0.01, lease_seconds), ts),
            ).fetchall()
            for row in rows:
                wake = WakeOperation._from_row(row)
                conn.execute(
                    "UPDATE wake_outbox SET status = 'pending',"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " not_before = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'delivering'"
                    " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                    (ts, ts, wake.id, max(0.01, lease_seconds), ts),
                )
                conn.execute(
                    "UPDATE tasks SET wake_status = 'pending'"
                    " WHERE id = ? AND wake_operation_id = ?",
                    (wake.task_id, wake.id),
                )
                task = self._fetch(conn, wake.task_id)
                if task is not None:
                    self._audit(
                        conn,
                        wake.task_id,
                        ts=ts,
                        from_status=task.status,
                        to_status=task.status,
                        worker=wake.owner,
                        note=f"wake recovered ({wake.id})",
                    )
            conn.execute("COMMIT")
        return len(rows)

    def claim_due_wake(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    ) -> WakeOperation | None:
        """Atomically claim the oldest due current wake operation."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            while True:
                row = conn.execute(
                    "SELECT * FROM wake_outbox"
                    " WHERE status = 'pending' AND not_before <= ?"
                    " ORDER BY created_at ASC, id ASC LIMIT 1",
                    (ts,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                wake = WakeOperation._from_row(row)
                task = self._fetch(conn, wake.task_id)
                if not self._wake_is_current(task, wake):
                    conn.execute(
                        "UPDATE wake_outbox SET status = 'stale', updated_at = ?,"
                        " last_error = 'task fence advanced' WHERE id = ?"
                        " AND status = 'pending'",
                        (ts, wake.id),
                    )
                    conn.execute(
                        "UPDATE tasks SET wake_status = 'stale'"
                        " WHERE id = ? AND wake_operation_id = ?",
                        (wake.task_id, wake.id),
                    )
                    if task is not None:
                        self._audit(
                            conn,
                            wake.task_id,
                            ts=ts,
                            from_status=task.status,
                            to_status=task.status,
                            worker=wake.owner,
                            note=f"wake stale ({wake.id})",
                        )
                    continue
                token = uuid.uuid4().hex
                cur = conn.execute(
                    "UPDATE wake_outbox SET status = 'delivering',"
                    " attempts = attempts + 1, delivery_token = ?,"
                    " delivery_expires_at = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (token, ts + max(0.01, lease_seconds), ts, wake.id),
                )
                if not cur.rowcount:
                    continue
                conn.execute(
                    "UPDATE tasks SET wake_status = 'delivering'"
                    " WHERE id = ? AND wake_operation_id = ?",
                    (wake.task_id, wake.id),
                )
                claimed = conn.execute(
                    "SELECT * FROM wake_outbox WHERE id = ?", (wake.id,)
                ).fetchone()
                conn.execute("COMMIT")
                return WakeOperation._from_row(claimed)

    def finish_wake(
        self,
        operation_id: str,
        delivery_token: str,
        *,
        delivered: bool,
        error: str | None = None,
        max_attempts: int = 8,
        retry_base: float = 1.0,
        now: float | None = None,
    ) -> WakeOperation:
        """Record delivery or retry a claimed wake with exponential backoff."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM wake_outbox WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such wake operation {operation_id!r}")
            wake = WakeOperation._from_row(row)
            if wake.status != "delivering" or wake.delivery_token != delivery_token:
                conn.execute("COMMIT")
                raise TaskError(f"wake operation {operation_id!r} is not held by this delivery")
            task = self._fetch(conn, wake.task_id)
            if not self._wake_is_current(task, wake):
                status = "stale"
                note = f"wake stale ({wake.id})"
                params = (status, ts, "task fence advanced", wake.id)
                conn.execute(
                    "UPDATE wake_outbox SET status = ?, updated_at = ?,"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = ? WHERE id = ?",
                    params,
                )
            elif delivered:
                status = "delivered"
                note = f"wake delivered ({wake.id})"
                conn.execute(
                    "UPDATE wake_outbox SET status = 'delivered', updated_at = ?,"
                    " delivered_at = ?, delivery_token = NULL,"
                    " delivery_expires_at = NULL, last_error = NULL"
                    " WHERE id = ?",
                    (ts, ts, wake.id),
                )
            elif wake.attempts >= max(1, max_attempts):
                status = "failed"
                note = f"wake failed ({wake.id})"
                conn.execute(
                    "UPDATE wake_outbox SET status = 'failed', updated_at = ?,"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = ? WHERE id = ?",
                    (ts, error or "delivery failed", wake.id),
                )
            else:
                status = "pending"
                delay = min(
                    60.0,
                    max(0.01, retry_base) * float(2 ** max(0, wake.attempts - 1)),
                )
                note = f"wake retry scheduled ({wake.id})"
                conn.execute(
                    "UPDATE wake_outbox SET status = 'pending', updated_at = ?,"
                    " not_before = ?, delivery_token = NULL,"
                    " delivery_expires_at = NULL, last_error = ?"
                    " WHERE id = ?",
                    (ts, ts + delay, error or "delivery failed", wake.id),
                )
            conn.execute(
                "UPDATE tasks SET wake_status = ? WHERE id = ? AND wake_operation_id = ?",
                (status, wake.task_id, wake.id),
            )
            if task is not None:
                self._audit(
                    conn,
                    wake.task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=wake.owner,
                    note=note,
                )
            result = conn.execute("SELECT * FROM wake_outbox WHERE id = ?", (wake.id,)).fetchone()
            conn.execute("COMMIT")
        return WakeOperation._from_row(result)

    def wake_metrics(self, *, now: float | None = None) -> dict[str, int | float | None]:
        """Return durable outbox counts and oldest pending age."""
        ts = self._now(now)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM wake_outbox GROUP BY status"
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM wake_outbox WHERE status IN ('pending', 'delivering')"
            ).fetchone()[0]
        counts = {
            "pending": 0,
            "delivering": 0,
            "delivered": 0,
            "failed": 0,
            "stale": 0,
        }
        counts.update({row["status"]: row["count"] for row in rows})
        return {
            **counts,
            "oldest_pending_age": (round(ts - oldest, 3) if oldest is not None else None),
        }

    def detach(self, task_id: str, *, now: float | None = None) -> Task:
        """Demote a hard worktree pin to a soft affinity (portability)."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            requires = [r for r in task.requires if not r.startswith("worktree:")]
            affinity = dict(task.affinity)
            if task.target_worktree:
                affinity["worktree"] = task.target_worktree
            conn.execute(
                "UPDATE tasks SET requires = ?, affinity = ?, target_worktree = NULL,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(requires), json.dumps(affinity), ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]
