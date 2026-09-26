"""``TaskQueue`` mixin: the Completion Review card's backend
(``confirm``/``reopen_completed``).

Split out of ``queue_lifecycle.py`` to keep that module under its
module-size cap; a pure mechanical extraction with no behavior change. Like
its sibling, this mixin is composed into :class:`agent_dispatch.queue.TaskQueue`
via multiple inheritance and relies on queue-owned storage helpers such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, ``self._now()``,
and ``self._transition()``; it is not usable standalone.
"""

from __future__ import annotations

import json

from .queue_common import Task, _check_expected_status, _task_transition_spec
from .queue_records import TaskError


class QueueCompletionReviewMixin:
    """``confirm``/``reopen_completed`` -- the Completion Review card's
    backend, for :class:`TaskQueue`."""

    def confirm(
        self,
        task_id: str,
        *,
        actor: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Corroborate a completion claim and close the task for good.

        The true lifecycle terminal beyond a provisional ``completed`` --
        see the agent-dispatch vision's *verify-the-completion-claim* / *The
        lifecycle*. Requires no owner (a completed task has none); ``actor``
        is recorded in the audit note only -- an evaluator's own identity
        for an automatic confirmation of emitter-driven work, or the
        operator's for a manual one via the Completion Review card.
        """
        allowed, to = _task_transition_spec("confirm")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            require_owner=False,
            now=now,
            note=f"confirmed by {actor}" if actor else "confirmed",
            expected_status=expected_status,
            expected_generation=expected_generation,
            idempotent_replay=True,
        )

    def reopen_completed(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        steer_fields: dict | None = None,
        sender: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """The Completion Review card's "Re-queue with steering" action (and
        an operator's plain disagreement with a completion claim more
        generally): the task returns to ``queued`` with its accumulated
        progress and durable goal preserved -- per
        *resume-the-goal-not-restart-it*, never restarting from nothing.

        The completion claim being reopened is superseded, not merely
        annotated: ``result``/``result_ref``/``completed_by``/
        ``completed_at`` are cleared so a later, genuine completion records
        its own fresh claim rather than colliding with the disputed one
        (see ``complete_with_outcome``'s same-result-required retry check).

        When ``steer_fields`` is given, it is recorded as an ordinary steer
        atomically with the reopen, so the next worker to claim the task
        sees the operator's new instructions immediately.
        """
        ts = self._now(now)
        allowed, to = _task_transition_spec("reopen_completed")
        allowed_set = set(allowed)
        note = f"reopen: {reason}" if reason else "reopen"
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
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot reopen a {task.status!r} task (allowed: {sorted(allowed_set)})"
                )
            if expected_generation is not None and task.generation != expected_generation:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} ownership incarnation changed")
            if steer_fields is not None:
                payload = json.dumps(steer_fields, separators=(",", ":"))
                conn.execute(
                    "INSERT INTO task_steer (task_id, ts, fields, sender) VALUES (?, ?, ?, ?)",
                    (task_id, ts, payload, sender),
                )
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, activity = NULL,"
                " activity_updated_at = ?, completed_at = NULL, result_ref = NULL,"
                " result = NULL, completed_by = NULL, awaiting_steer = 0,"
                " card_draft = NULL WHERE id = ?",
                (to, ts, ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=to,
                worker=sender,
                note=note,
            )
            result = self._fetch(conn, task_id)
            assert result is not None
            conn.execute("COMMIT")
        return result
