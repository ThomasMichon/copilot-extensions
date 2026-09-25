"""``TaskQueue`` mixin: task lifecycle, ownership, and progress methods.

Extracted from :mod:`agent_dispatch.queue` as the next large responsibility
band after the steering/wake slice: transitions between task states, the
ownership/session fences they enforce, and the durable progress trail all hang
off one shared transition primitive.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned storage helpers such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, ``self._now()``,
``self._record_attachment()``, and ``self._enqueue_wake()`` and is not usable
standalone.
"""

from __future__ import annotations

import json
import sqlite3

from .queue_common import (
    PROGRESS_SUMMARY_MAX,
    CompletionOutcome,
    StructuredResult,
    Task,
    _check_expected_status,
    _claimant_worktree_machine,
    _clip,
    _progress_snapshot,
    _task_transition_spec,
)
from .queue_records import SpawnState, Status, TaskError


class QueueLifecycleMixin:
    """Lifecycle, ownership, and progress methods for :class:`TaskQueue`."""

    def approve(self, task_id: str, *, now: float | None = None) -> Task:
        """Move a ``proposed`` task to ``queued`` (makes it claimable)."""
        allowed, to = _task_transition_spec("approve")
        return self._transition(
            task_id, allowed=allowed, to=to, now=now, note="approve", idempotent_replay=True
        )

    def start(
        self,
        task_id: str,
        worker_id: str,
        *,
        owner_session_id: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Move a ``claimed`` task to ``started`` (owner must match)."""
        ts = self._now(now)
        extra: dict[str, object] = {"last_seen_at": ts}
        if owner_session_id is not None:
            extra["owner_session_id"] = owner_session_id
        allowed, to = _task_transition_spec("start")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=now,
            note="start",
            stamp="started_at",
            extra=extra,
            idempotent_replay=True,
            reject_if_held=True,
        )

    def complete(
        self,
        task_id: str,
        worker_id: str,
        *,
        result_ref: str | None = None,
        result: StructuredResult | None = None,
        expected_status: str | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Complete work and return its task snapshot."""
        return self.complete_with_outcome(
            task_id,
            worker_id,
            result_ref=result_ref,
            result=result,
            expected_status=expected_status,
            expected_owner_session_id=expected_owner_session_id,
            expected_generation=expected_generation,
            now=now,
        ).task

    def complete_with_outcome(
        self,
        task_id: str,
        worker_id: str,
        *,
        result_ref: str | None = None,
        result: StructuredResult | None = None,
        expected_status: str | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> CompletionOutcome:
        """Complete active or suspended work (owner must match)."""
        encoded_result = self._encode_result(result)
        allowed, _to = _task_transition_spec("complete")
        allowed = set(allowed)
        if expected_status is not None:
            if expected_status not in allowed:
                raise TaskError(f"cannot expect {expected_status!r} when completing a task")
            allowed = {expected_status}
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")

            if task.status == Status.COMPLETED and encoded_result is not None:
                completing_owner = task.completed_by
                if completing_owner is None:
                    completion_workers = self._completion_event_workers(conn, task_id)
                    if not completion_workers:
                        conn.execute("COMMIT")
                        raise TaskError(
                            f"task {task_id!r} has no unambiguous completing owner"
                            " in its completion events; cannot safely record a result"
                        )
                    if len(completion_workers) != 1:
                        conn.execute("COMMIT")
                        owners = ", ".join(repr(owner) for owner in completion_workers)
                        raise TaskError(
                            f"task {task_id!r} has ambiguous completing owners"
                            f" in its completion events ({owners});"
                            " cannot safely record a result"
                        )
                    completing_owner = completion_workers[0]
                if completing_owner != worker_id:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"task {task_id!r} was completed by {completing_owner!r},"
                        f" not {worker_id!r}"
                    )
                row = conn.execute(
                    "SELECT result, result_ref FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                current_result = row["result"]
                current_ref = row["result_ref"]
                if result_ref is not None and current_ref not in (None, result_ref):
                    conn.execute("COMMIT")
                    raise TaskError(f"task {task_id!r} already has a different result_ref")
                if current_result is not None:
                    if current_result != encoded_result:
                        conn.execute("COMMIT")
                        raise TaskError(f"task {task_id!r} already has a different result")
                    if task.completed_by is None:
                        conn.execute(
                            "UPDATE tasks SET completed_by = ?"
                            " WHERE id = ? AND completed_by IS NULL",
                            (completing_owner, task_id),
                        )
                        task = self._fetch(conn, task_id)
                        assert task is not None
                    conn.execute("COMMIT")
                    return CompletionOutcome(task, None)
                conn.execute(
                    "UPDATE tasks SET result = ?, result_ref = COALESCE(result_ref, ?),"
                    " completed_by = COALESCE(completed_by, ?), updated_at = ?"
                    " WHERE id = ?",
                    (encoded_result, result_ref, completing_owner, ts, task_id),
                )
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=Status.COMPLETED,
                    to_status=Status.COMPLETED,
                    worker=worker_id,
                    note="complete retry: result recorded",
                )
                completed = self._fetch(conn, task_id)
                assert completed is not None
                conn.execute("COMMIT")
                return CompletionOutcome(completed, "task.result_recorded")

            if task.status not in allowed:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot complete a {task.status!r} task (allowed: {sorted(allowed)})"
                )
            if task.owner not in (None, worker_id):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if expected_generation is not None and (
                task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
            ):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} ownership incarnation changed")

            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, activity = NULL,"
                " activity_updated_at = ?, completed_at = ?, result_ref = ?,"
                " result = ?, completed_by = ?, owner = NULL,"
                " lease_expires_at = NULL WHERE id = ?",
                (
                    Status.COMPLETED,
                    ts,
                    ts,
                    ts,
                    result_ref,
                    encoded_result,
                    worker_id,
                    task_id,
                ),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=Status.COMPLETED,
                worker=worker_id,
                note="complete",
            )
            completed = self._fetch(conn, task_id)
            assert completed is not None
            conn.execute("COMMIT")
        return CompletionOutcome(completed, "task.completed")

    @staticmethod
    def _completion_event_workers(conn: sqlite3.Connection, task_id: str) -> list[str]:
        """Return distinct owners from authoritative completion transitions."""
        rows = conn.execute(
            "SELECT DISTINCT worker FROM task_events"
            " WHERE task_id = ? AND to_status = ? AND from_status <> ?"
            " AND worker IS NOT NULL ORDER BY worker",
            (task_id, Status.COMPLETED, Status.COMPLETED),
        )
        return [str(row["worker"]) for row in rows]

    def suspend(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
        reject_pending_steer: bool = True,
        now: float | None = None,
    ) -> Task:
        """Park a ``started`` task as dormant while preserving its owner."""
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("suspend requires a non-empty reason")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._fetch(conn, task_id)
            if current is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            try:
                _check_expected_status(current, expected_status)
            except TaskError:
                conn.execute("COMMIT")
                raise
            if current.status == Status.SUSPENDED and current.owner == worker_id:
                self._audit(
                    conn,
                    task_id,
                    ts=self._now(now),
                    from_status=Status.SUSPENDED,
                    to_status=Status.SUSPENDED,
                    worker=worker_id,
                    note=f"suspend: {meaningful}",
                )
                result = self._fetch(conn, task_id)
                conn.execute("COMMIT")
                return result  # type: ignore[return-value]
            conn.execute("COMMIT")
        allowed, to = _task_transition_spec("suspend")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=now,
            note=f"suspend: {meaningful}",
            extra={"lease_expires_at": None, "last_liveness": None},
            expected_generation=expected_generation,
            expected_owner_session_id=expected_owner_session_id,
            reject_pending_steer=reject_pending_steer,
        )

    def resume(
        self,
        task_id: str,
        worker_id: str,
        *,
        wake_requested: bool = False,
        wake_message: str | None = None,
        adopt_owner_session_id: str | None = None,
        reuse_session: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Wake an owned ``suspended`` task back to ``started``."""
        ts = self._now(now)
        extra: dict[str, object] = {
            "lease_expires_at": ts + self.lease_seconds,
            "last_seen_at": ts,
            "last_liveness": None,
            "resume_requested": 0,
        }
        if adopt_owner_session_id is not None:
            extra["owner_session_id"] = adopt_owner_session_id
        allowed, to = _task_transition_spec("resume")
        result = self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=ts,
            note="resume",
            extra=extra,
            bump_generation=adopt_owner_session_id is not None,
            expected_owner_session_id=expected_owner_session_id,
            expected_generation=expected_generation,
            reembody_headless_on_wake=not reuse_session,
            wake_requested=wake_requested,
            wake_message=wake_message,
            reject_if_held=True,
            idempotent_replay=adopt_owner_session_id is None,
        )
        self._notify_owned_transition()
        return result

    def release_suspended(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Release a suspended task to ``queued`` for a replacement worker."""
        note = _clip(reason, PROGRESS_SUMMARY_MAX) or "release suspended task"
        allowed, to = _task_transition_spec("release_suspended")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=now,
            note=note,
            extra={
                "owner": None,
                "owner_session_id": None,
                "lease_expires_at": None,
                "claimed_at": None,
                "last_liveness": None,
                "resume_requested": 0,
            },
            release_spawn=True,
            release_spawn_detail="task released from suspension",
            reject_if_held=True,
            idempotent_replay=True,
        )

    def set_hold(
        self,
        task_id: str,
        *,
        reason: str,
        actor: str,
        expected_status: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Set a durable, operator-owned hold on ``task_id``."""
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("set_hold requires a non-empty reason")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            _check_expected_status(task, expected_status)
            if task.status in Status.TERMINAL:
                conn.execute("COMMIT")
                raise TaskError(f"cannot hold a {task.status!r} task {task_id!r}")
            conn.execute(
                "UPDATE tasks SET hold_reason = ?, hold_actor = ?, hold_at = ?,"
                " updated_at = ? WHERE id = ?",
                (meaningful, actor, ts, ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=actor,
                note=f"hold: {meaningful}",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def clear_hold(
        self,
        task_id: str,
        *,
        actor: str | None = None,
        expected_status: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Clear a hold set by :meth:`set_hold`."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.hold_reason is None:
                conn.execute("COMMIT")
                return task
            _check_expected_status(task, expected_status)
            conn.execute(
                "UPDATE tasks SET hold_reason = NULL, hold_actor = NULL,"
                " hold_at = NULL, updated_at = ? WHERE id = ?",
                (ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=actor,
                note=f"unhold (was: {task.hold_reason})",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def yield_task(
        self,
        task_id: str,
        worker_id: str,
        *,
        note: str | None = None,
        exclude: str | None = None,
        release_spawn: bool = True,
        now: float | None = None,
    ) -> Task:
        """Return an owned task to ``queued`` with updates."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._fetch(conn, task_id)
            if (
                current is not None
                and current.status == Status.SUSPENDED
                and current.awaiting_steer
                and current.owner == worker_id
            ):
                self._audit(
                    conn,
                    task_id,
                    ts=self._now(now),
                    from_status=Status.SUSPENDED,
                    to_status=Status.SUSPENDED,
                    worker=worker_id,
                    note=note or "yield after blocking card: already suspended",
                )
                result = self._fetch(conn, task_id)
                conn.execute("COMMIT")
                return result  # type: ignore[return-value]
            conn.execute("COMMIT")
        extra: dict[str, object] = {
            "owner": None,
            "owner_session_id": None,
            "lease_expires_at": None,
            "claimed_at": None,
            "last_liveness": None,
        }
        if exclude:
            current = self.get(task_id)
            existing = list(current.excludes) if current is not None else []
            if exclude not in existing:
                existing.append(exclude)
            extra["excludes"] = json.dumps(existing)
        allowed, to = _task_transition_spec("yield_task")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=now,
            note=note or "yield",
            extra=extra,
            release_spawn=release_spawn,
            release_spawn_detail="task yielded",
            idempotent_replay=True,
        )

    def abandon(
        self,
        task_id: str,
        *,
        worker_id: str | None = None,
        permitted: bool = False,
        reason: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Move a task to terminal ``abandoned`` -- requires ``permitted=True``.
        See :meth:`abandon_with_outcome` for a replay-aware sibling.
        """
        return self.abandon_with_outcome(
            task_id, worker_id=worker_id, permitted=permitted, reason=reason,
            expected_status=expected_status, expected_generation=expected_generation,
            expected_owner_session_id=expected_owner_session_id, now=now,
        ).task

    def abandon_with_outcome(
        self,
        task_id: str,
        *,
        worker_id: str | None = None,
        permitted: bool = False,
        reason: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
        now: float | None = None,
    ) -> CompletionOutcome:
        """Like :meth:`abandon`, but returns a :class:`CompletionOutcome`
        (PR #3248 review) whose ``event_type`` is ``"task.abandoned"`` on a
        genuine transition or ``None`` on an idempotent replay -- so a
        terminal-transition side effect (e.g. releasing a context-handoff
        claim) fires once per genuine abandon, never once per retry.
        """
        if not permitted:
            raise TaskError("abandon requires permission (permitted=True)")
        allowed, to = _task_transition_spec("abandon")
        result = self._transition(
            task_id, allowed=allowed, to=to, worker_id=worker_id,
            require_owner=False, now=now, note=reason or "abandon",
            extra={"owner": None, "lease_expires_at": None},
            expected_generation=expected_generation,
            expected_owner_session_id=expected_owner_session_id,
            expected_status=expected_status,
            idempotent_replay=True, report_replay=True,
        )
        assert isinstance(result, tuple)  # noqa: S101
        task, is_replay = result
        return CompletionOutcome(task, None if is_replay else "task.abandoned")

    def reset(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Reset a task back to ``proposed`` for a fresh attempt."""
        allowed, to = _task_transition_spec("reset")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            require_owner=False,
            now=now,
            note=f"reset: {reason}" if reason else "reset",
            extra={
                "owner": None,
                "owner_session_id": None,
                "lease_expires_at": None,
                "claimed_at": None,
                "started_at": None,
                "last_liveness": None,
                "card": None,
                "card_draft": None,
                "awaiting_steer": 0,
                "resume_requested": 0,
            },
            expected_generation=expected_generation,
            expected_owner_session_id=expected_owner_session_id,
            expected_status=expected_status,
            reject_if_held=True,
            idempotent_replay=True,
            release_spawn=True,
            release_spawn_detail="task reset to proposed",
        )

    def heartbeat(self, task_id: str, worker_id: str, *, now: float | None = None) -> Task:
        """Extend the lease on a held task the worker still owns."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot heartbeat a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, updated_at = ? WHERE id = ?",
                (ts + self.lease_seconds, ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def bind_owner_session(
        self,
        task_id: str,
        worker_id: str,
        owner_session_id: str,
        *,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Bind a held headless task to its exact bridge session identity."""
        if not owner_session_id:
            raise TaskError("owner session id must be non-empty")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot bind owner session on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if expected_generation is not None and task.generation != expected_generation:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} ownership incarnation changed")
            if task.owner_session_id not in {None, owner_session_id}:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} already bound to another owner session")
            conn.execute(
                "UPDATE tasks SET owner_session_id=?, updated_at=? WHERE id=?",
                (owner_session_id, ts, task_id),
            )
            attach_machine, attach_worktree = _claimant_worktree_machine(task)
            self._record_attachment(
                conn,
                task_id,
                ts=ts,
                old_session_id=task.owner_session_id,
                new_session_id=owner_session_id,
                worktree_id=attach_worktree,
                machine=attach_machine,
                detach_reason="rebound",
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=f"owner session bound ({owner_session_id})",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def set_activity(
        self,
        task_id: str,
        activity: str | None,
        *,
        reservation_key: str,
        now: float | None = None,
    ) -> Task:
        """Publish activity fenced to this task's active spawn reservation."""
        if activity not in {None, "ACTIVE", "IDLE", "STALLED"}:
            raise TaskError(
                f"invalid task activity {activity!r} (allowed: ACTIVE, IDLE, STALLED, or null)"
            )
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status == Status.SUSPENDED and activity not in {None, "IDLE"}:
                conn.execute("COMMIT")
                raise TaskError(f"cannot set active activity on suspended task {task_id!r}")
            reservation = conn.execute(
                "SELECT task_id, state FROM spawn_reservations WHERE key = ?",
                (reservation_key,),
            ).fetchone()
            if (
                reservation is None
                or reservation["task_id"] != task_id
                or reservation["state"] != SpawnState.SPAWNED
            ):
                conn.execute("COMMIT")
                raise TaskError(
                    f"activity update requires task {task_id!r}'s active spawned "
                    f"reservation, got {reservation_key!r}"
                )
            conn.execute(
                "UPDATE tasks SET activity = ?, activity_updated_at = ? WHERE id = ?",
                (activity, ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def record_progress(
        self,
        task_id: str,
        worker_id: str,
        *,
        phase: str,
        summary: str,
        blocker: str | None = None,
        pr: str | None = None,
        detail: str | None = None,
        extend_lease: bool = True,
        now: float | None = None,
    ) -> Task:
        """Record a bounded progress beat on a held task the worker owns."""
        ts = self._now(now)
        snapshot = _progress_snapshot(phase, summary, blocker=blocker, pr=pr, ts=ts)
        payload = json.dumps(snapshot, separators=(",", ":"))
        log_detail = _clip(detail, PROGRESS_SUMMARY_MAX)
        if log_detail is None:
            parts = []
            if snapshot.get("blocker"):
                parts.append(f"blocker: {snapshot['blocker']}")
            if snapshot.get("pr"):
                parts.append(f"pr: {snapshot['pr']}")
            log_detail = "; ".join(parts) or None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot record progress on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if extend_lease:
                conn.execute(
                    "UPDATE tasks SET latest_progress = ?, lease_expires_at = ?,"
                    " last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (payload, ts + self.lease_seconds, ts, ts, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET latest_progress = ?, last_seen_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (payload, ts, ts, task_id),
                )
            conn.execute(
                "INSERT INTO task_progress (task_id, ts, phase, summary, detail, worker) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    ts,
                    snapshot.get("phase") or None,
                    snapshot["summary"],
                    log_detail,
                    worker_id,
                ),
            )
            phase_tag = f"[{snapshot['phase']}] " if snapshot.get("phase") else ""
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=f"progress: {phase_tag}{snapshot['summary']}",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def _transition(
        self,
        task_id: str,
        *,
        allowed: list[str] | tuple[str, ...] | set[str] | frozenset[str],
        to: str,
        worker_id: str | None = None,
        require_owner: bool = True,
        now: float | None = None,
        note: str | None = None,
        stamp: str | None = None,
        extra: dict[str, object] | None = None,
        release_spawn: bool = False,
        release_spawn_detail: str = "task released from suspension",
        wake_requested: bool = False,
        wake_message: str | None = None,
        bump_generation: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        expected_status: str | None = None,
        reembody_headless_on_wake: bool = False,
        reject_pending_steer: bool = False,
        reject_if_held: bool = False,
        idempotent_replay: bool = False,
        report_replay: bool = False,
    ) -> Task | tuple[Task, bool]:
        """Generic fenced transition helper.

        ``report_replay=True``: returns ``(Task, bool)`` -- ``True`` iff this
        call hit the idempotent-replay no-op branch below (PR #3248 review).
        Defaults to ``False``; every existing ``-> Task`` call site is
        unaffected.
        """
        ts = self._now(now)
        allowed_set = set(allowed)
        wake_enqueued = False
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
            if task.status not in allowed_set:
                if idempotent_replay and task.status == to:
                    owner_ok = (
                        not require_owner or worker_id is None or task.owner in (None, worker_id)
                    )
                    generation_ok = expected_generation is None or (
                        task.generation == expected_generation
                        and task.owner_session_id == expected_owner_session_id
                    )
                    if owner_ok and generation_ok:
                        self._audit(
                            conn,
                            task_id,
                            ts=ts,
                            from_status=task.status,
                            to_status=to,
                            worker=worker_id,
                            note=f"{note or to} (idempotent replay, already {to})",
                        )
                        result = self._fetch(conn, task_id)
                        conn.execute("COMMIT")
                        if report_replay:
                            return result, True  # type: ignore[return-value]
                        return result  # type: ignore[return-value]
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot {note or to} a {task.status!r} task (allowed: {sorted(allowed_set)})"
                )
            if require_owner and worker_id is not None and task.owner not in (None, worker_id):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if reject_pending_steer:
                pending = conn.execute(
                    "SELECT 1 FROM task_steer WHERE task_id = ? AND taken = 0 LIMIT 1",
                    (task_id,),
                ).fetchone()
                if pending is not None:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"cannot suspend task {task_id!r}: pending steer; take it and continue"
                    )
            if reject_if_held and task.hold_reason is not None:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is held ({task.hold_reason!r}); "
                    "clear_hold() before resuming/releasing it"
                )
            if expected_generation is not None and (
                task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
            ):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} ownership incarnation changed")
            if (
                reembody_headless_on_wake
                and task.owner_session_id is None
                and self._has_headless_reservation(conn, task_id)
            ):
                to = Status.SUSPENDED
                note = "resume requested for cold headless owner"
                extra = {
                    "lease_expires_at": None,
                    "last_liveness": None,
                    "resume_requested": 1,
                }
                wake_requested = False
            sets = ["status = ?", "updated_at = ?"]
            params: list[object] = [to, ts]
            if bump_generation:
                sets.append("generation = generation + 1")
            if to not in Status.HELD:
                sets.extend(["activity = NULL", "activity_updated_at = ?"])
                params.append(ts)
            if stamp is not None:
                sets.append(f"{stamp} = ?")
                params.append(ts)
            for col, val in (extra or {}).items():
                sets.append(f"{col} = ?")
                params.append(val)
            params.append(task_id)
            conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)  # noqa: S608
            if "owner_session_id" in (extra or {}):
                attach_machine, attach_worktree = _claimant_worktree_machine(task)
                self._record_attachment(
                    conn,
                    task_id,
                    ts=ts,
                    old_session_id=task.owner_session_id,
                    new_session_id=extra["owner_session_id"],  # type: ignore[index]
                    worktree_id=attach_worktree,
                    machine=attach_machine,
                    detach_reason=note or to,
                )
            if release_spawn:
                conn.execute(
                    "UPDATE spawn_reservations SET state = ?, "
                    "release_requested = 1, "
                    "release_disposition = COALESCE(release_disposition, ?), "
                    "conclusion_state = COALESCE(conclusion_state, ?), "
                    "updated_at = ?, detail = COALESCE(detail, ?) WHERE task_id = ?"
                    " AND state IN (?, ?, ?)",
                    (
                        SpawnState.RELEASING,
                        "settled",
                        "pending",
                        ts,
                        release_spawn_detail,
                        task_id,
                        SpawnState.RESERVING,
                        SpawnState.SPAWNED,
                        SpawnState.COLD,
                    ),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=to,
                worker=worker_id,
                note=note,
            )
            result = self._fetch(conn, task_id)
            if wake_requested:
                self._enqueue_wake(
                    conn,
                    result,  # type: ignore[arg-type]
                    message=wake_message,
                    ts=ts,
                )
                wake_enqueued = True
                result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        if wake_enqueued:
            self._notify_wake()
        if report_replay:
            return result, False  # type: ignore[return-value]
        return result  # type: ignore[return-value]
