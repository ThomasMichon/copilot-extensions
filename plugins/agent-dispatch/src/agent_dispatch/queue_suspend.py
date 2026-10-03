"""``TaskQueue`` mixin: suspend/resume and the cooldown monitor reconciler.

Extracted from :mod:`agent_dispatch.queue_lifecycle` (module-size discipline
-- see ``AGENTS.md``'s note on new additions going into a sibling mixin
rather than a near-ceiling module) as its own coherent responsibility band:
parking a task dormant, waking it back up, and -- Phase 3 of
``efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md`` --
the periodic pass that auto-resumes a suspended task whose default
**cooldown monitor** (see :mod:`agent_dispatch.monitors`) has elapsed.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned primitives such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, ``self._now()``,
``self._transition()``, and ``self._notify_owned_transition()`` (all defined
by sibling mixins) and is not usable standalone.

**Why a bare cooldown-due resume can reuse :meth:`resume` unchanged:**
``resume()`` already branches, inside ``_transition``'s
``reembody_headless_on_wake`` handling, between an interactive owner (a live
``owner_session_id`` -- transitions straight to ``started`` and optionally
enqueues a wake nudge) and a cold-headless owner (no live session -- stays
``suspended`` but flips ``resume_requested`` for the supervisor's own
``release_resumed_cold_tasks`` poll to pick up). The reconciler below never
needs to re-derive that split itself; it just calls ``resume()`` on the
task's own current owner once the monitor is due.
"""

from __future__ import annotations

import math
import logging
from collections.abc import Iterator

from .monitors import DEFAULT_SUSPEND_COOLDOWN_SECONDS, MonitorKind, suspend_monitor_columns
from .queue_common import (
    PROGRESS_SUMMARY_MAX,
    Task,
    _TASK_BULK_SELECT,
    _check_expected_status,
    _clip,
    _task_transition_spec,
)
from .queue_records import Status, TaskError
from .pr_observation_store import observation_store_path
from .reviewer_loops import (
    active_reviewer_loop_lifecycle_configs,
    reviewer_loop_deadline,
    reviewer_loop_lifecycle_for_task,
    reviewer_loop_runtime_scope,
)

log = logging.getLogger("agent-dispatch.queue-suspend")


class QueueSuspendMixin:
    """Suspend, resume, and cooldown-monitor reconciliation for :class:`TaskQueue`."""

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
        cooldown_seconds: float | None = DEFAULT_SUSPEND_COOLDOWN_SECONDS,
        now: float | None = None,
    ) -> Task:
        """Park a ``started`` task as dormant while preserving its owner.

        Per the vision's *suspension-requires-a-monitor* behavior, a bare
        suspend (``cooldown_seconds`` left at its default) always gets a
        real, bounded, checkable monitor -- never an unwatched idle. Pass
        ``cooldown_seconds=None`` only when the caller has arranged its own,
        more specific wait (e.g. an operator steer answer already pending).
        """
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("suspend requires a non-empty reason")
        ts = self._now(now)
        monitor_columns = suspend_monitor_columns(now=ts, cooldown_seconds=cooldown_seconds)
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
                conn.execute(
                    "UPDATE tasks SET monitor_kind = ?, monitor_not_before = ?,"
                    " updated_at = ? WHERE id = ?",
                    (
                        monitor_columns["monitor_kind"],
                        monitor_columns["monitor_not_before"],
                        ts,
                        task_id,
                    ),
                )
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
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
            now=ts,
            note=f"suspend: {meaningful}",
            extra={"lease_expires_at": None, "last_liveness": None, **monitor_columns},
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
            "monitor_kind": None,
            "monitor_not_before": None,
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

    def reconcile_cooldowns(self, *, now: float | None = None) -> int:
        """Auto-resume every ``suspended`` task whose cooldown has elapsed.

        A companion reconciliation pass to the liveness GC / orphan reap /
        handoff-fallback loops (see :mod:`agent_dispatch.coordinator_loops`):
        this is what makes a bare suspend a genuine round-robin time-slice
        rather than a wait nothing ever revisits. Reuses :meth:`resume`
        unchanged for both an interactive and a cold-headless owner -- see
        this module's docstring. A task that raced with a manual
        resume/steer/abandon in between is simply skipped this pass.
        """
        ts = self._now(now)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, owner FROM tasks WHERE status = ? AND monitor_kind = ?"
                " AND monitor_not_before IS NOT NULL AND monitor_not_before <= ?",
                (Status.SUSPENDED, MonitorKind.COOLDOWN.value, ts),
            ).fetchall()
        resumed = 0
        for row in rows:
            task_id, owner = row["id"], row["owner"]
            if not owner:
                continue
            try:
                self.resume(
                    task_id,
                    owner,
                    wake_requested=True,
                    wake_message=(
                        f"Task {task_id}'s cooldown elapsed while it was "
                        "suspended. Re-read it and resume from the recorded "
                        "state."
                    ),
                    now=ts,
                )
            except TaskError:
                continue
            resumed += 1
        return resumed

    def reconcile_reviewer_deadlines(self, *, now: float | None = None) -> int:
        """Wake suspended reviewer tasks once their stale deadline elapses."""
        ts = self._now(now)
        current_machine, current_env = reviewer_loop_runtime_scope()
        registrations = active_reviewer_loop_lifecycle_configs(
            self,
            current_machine=current_machine,
            current_env=current_env,
        )
        if not registrations:
            return 0
        resumed = 0
        for task in self._iter_suspended_reviewer_candidates():
            try:
                if (
                    not task.owner
                    or not task.evaluator_ref
                    or task.resume_requested
                    or task.wake_status in {"pending", "delivering"}
                ):
                    continue
                config = reviewer_loop_lifecycle_for_task(
                    registrations,
                    repo=task.repo,
                    evaluator_ref=task.evaluator_ref,
                )
                if config is None or config.stale_after_days is None:
                    continue
                deadline = reviewer_loop_deadline(
                    {
                        "task": {
                            "payload_inline": self.read_payload(task),
                            "payload_ref": task.payload_ref,
                        }
                    },
                    stale_after_days=config.stale_after_days,
                    observation_store_path=observation_store_path(self.db_path),
                )
                if deadline is None or not math.isfinite(deadline) or deadline > ts:
                    continue
                wakes = [
                    wake
                    for wake in self.list_run_waiter_wakes(task.id)
                    if wake.status in {"pending", "delivering"}
                ]
                if wakes:
                    continue
                message = (
                    f"Task {task.id}'s reviewer stale deadline elapsed while it was "
                    "suspended. Re-check the change and resolve it instead of leaving "
                    "the review parked."
                )
                result = self.supersede_run_waiter_with_wake(
                    task.id,
                    reason="reviewer stale deadline elapsed",
                    message=message,
                    sender="agent-dispatch-reviewer-loop",
                    now=ts,
                )
                if result is None:
                    self.resume(
                        task.id,
                        task.owner,
                        wake_requested=True,
                        wake_message=message,
                        now=ts,
                    )
            except TaskError:
                continue
            except Exception:
                log.warning(
                    "reviewer stale reconcile skipped task %s after data/read failure",
                    task.id,
                    exc_info=True,
                )
                continue
            resumed += 1
        return resumed

    def _iter_suspended_reviewer_candidates(
        self, *, batch_size: int = 500
    ) -> Iterator[Task]:
        cursor: tuple[float, str] | None = None
        with self._connect() as conn:
            while True:
                params: list[object] = [Status.SUSPENDED, batch_size]
                cursor_clause = ""
                if cursor is not None:
                    params = [Status.SUSPENDED, cursor[0], cursor[0], cursor[1], batch_size]
                    cursor_clause = (
                        " AND (created_at < ? OR (created_at = ? AND id < ?))"
                    )
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT} FROM tasks"
                    " WHERE status = ? AND require_verification = 1"
                    " AND evaluator_ref IS NOT NULL"
                    f"{cursor_clause}"
                    " ORDER BY created_at DESC, id DESC LIMIT ?",
                    params,
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    yield Task._from_row(row)
                tail = rows[-1]
                cursor = (float(tail["created_at"]), str(tail["id"]))
                if len(rows) < batch_size:
                    break
