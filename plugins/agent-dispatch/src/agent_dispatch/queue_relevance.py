"""Atomic source-relevance decisions; transport and owner closeout stay separate.

The coordinator also admits effective local sources through registered scope.
Emitters opt in through the receipt owner; periodic callers remain separate.
Callers judge the backing object; queue history
decides resource-free abandonment or original-owner closeout on existing outboxes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
import math
import sqlite3
from typing import TYPE_CHECKING, Any, cast

from .queue_common import CompletionOutcome, Task
from .queue_records import Status, TaskError
from .queue_relevance_binding import capture_target, closeout_detail
from .queue_relevance_registration import (
    RelevanceAdmission, RelevanceRegistration, registration_revision, resolve_registration,
)

if TYPE_CHECKING:
    from .queue import TaskQueue
    from .queue_common import WakeOperation


@dataclass(frozen=True)
class RelevanceScope:
    repo: str
    source: str
    origin_ref: str


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: object, name: str, limit: int = 4096) -> None:
    if (
        not isinstance(value, str) or not value.strip() or "\0" in value
        or len(value.encode("utf-8")) > limit
    ):
        raise TaskError(f"relevance {name} must be nonempty, NUL-free and at most {limit} bytes")


def closeout_request(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
    ).fetchone()
    return row


def require_relevant_goal(
    conn: sqlite3.Connection, task_id: str, *, owner_wake: WakeOperation | None = None,
    closeout_start: bool = False,
) -> None:
    """Refuse the original goal unless a body is accepting the task to close it out.

    ``closeout_start`` marks start, retry and resume paths for a body that already
    holds this task's assignment or acquisition. A pending closeout no longer
    blocks them: the accepting body becomes the closeout owner. New claimants
    and a supervisor finalize-first fence remain refused.
    """
    row = closeout_request(conn, task_id)
    if row is not None:
        held = conn.execute("SELECT hold_reason FROM tasks WHERE id=?", (task_id,)).fetchone()
        if held is not None and held["hold_reason"] is None:
            from .queue_relevance_delivery import closeout_wake
            if owner_wake is not None and owner_wake.task_id == task_id and closeout_wake(conn, owner_wake):
                return
            if closeout_start and row["acknowledgement"] is None:
                from .queue_relevance_finalize_first import finalize_fenced
                if not finalize_fenced(conn, task_id):
                    return
        raise TaskError("task relevance requires owner closeout, not restarting its original goal")


def _revision(conn: sqlite3.Connection, task_id: str) -> int:
    return int(conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id=?", (task_id,),
    ).fetchone()[0])


_LIVE_CUSTODY = (
    ("spawn_reservations", "state IN ('reserving','spawned','cold','releasing')"),
    ("task_attachments", "detached_at IS NULL"),
    ("execution_send_custody", "state IN ('pending','unknown')"),
    ("run_waiters", "state NOT IN ('retired','superseded')"),
    ("wake_outbox", "status IN ('pending','delivering') OR execution_send_state IN ('pending','unknown')"),
    ("run_waiter_wakes", "status IN ('pending','delivering')"),
)


def _custody(conn: sqlite3.Connection, task: Task) -> list[str]:
    """Live, accountable custody only; settled history is not a worker to close out.

    An assigned worker, an in-flight or unknown acquisition, an attached native
    session, pending send custody or an active waiter each needs a closeout.
    Earlier generations' claims, released reservations, delivered wakes and
    producer-held resource keys (released after terminal reconciliation) do not.
    """
    retained = []
    if (
        task.status in {Status.STARTED, Status.SUSPENDED}
        or task.owner or task.owner_session_id
    ):
        retained.append("worker_assigned")
    for table, live in _LIVE_CUSTODY:
        # Fixed owner tables and predicates, not caller SQL.
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE task_id=? AND ({live}) LIMIT 1", (task.id,),
        ).fetchone():
            retained.append(table)
    return retained


class QueueRelevanceMixin:
    if TYPE_CHECKING:
        _connect: Callable[[], sqlite3.Connection]
        _now: Callable[[float | None], float]
        _fetch: Callable[[sqlite3.Connection, str], Task | None]
        _audit: Callable[..., None]
        _notify_owned_transition: Callable[[], None]
        _notify_wake: Callable[[], None]

    @staticmethod
    def _migrate_task_relevance(conn: sqlite3.Connection) -> None:
        for name, table, columns in (
            ("idx_task_events_task", "task_events", "task_id, id"),
            ("idx_acquisition_receipts_task", "acquisition_receipts", "task_id"),
            ("idx_execution_send_task", "execution_send_custody", "task_id"),
        ):
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_relevance_requests ("
            "task_id TEXT NOT NULL, request_id TEXT NOT NULL, request_json TEXT NOT NULL,"
            "result_json TEXT NOT NULL, PRIMARY KEY(task_id, request_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_relevance_closeouts ("
            "task_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, generation INTEGER NOT NULL,"
            "owner TEXT, owner_session_id TEXT, steer_id INTEGER NOT NULL, created_at REAL NOT NULL)"
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(task_relevance_closeouts)")}
        for column in ("resource_binding", "readiness", "acknowledgement"):
            if column in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE task_relevance_closeouts ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        from .queue_relevance_finalize_first import migrate as migrate_finalize_first
        migrate_finalize_first(conn)

    def prepare_relevance_closeout(
        self, task_id: str, *, request_id: str, worker_id: str, native_id: str,
        expected_generation: int, authority_guard: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from .queue_relevance_readiness import prepare
        return prepare(
            cast("TaskQueue", self), task_id, request_id=request_id, worker_id=worker_id, native_id=native_id,
            expected_generation=expected_generation,
            authority_guard=authority_guard,
        )

    def finish_relevance_closeout(
        self, task_id: str, *, request_id: str, worker_id: str, native_id: str,
        expected_generation: int, authority_guard: Callable[[], None] | None = None,
    ) -> CompletionOutcome:
        from .queue_relevance_acknowledgement import finish
        return finish(
            cast("TaskQueue", self), task_id, request_id=request_id, worker_id=worker_id, native_id=native_id,
            expected_generation=expected_generation,
            authority_guard=authority_guard,
        )

    def relevance_snapshot(
        self, task_id: str, *, registration: RelevanceRegistration | None = None,
        disabled_ids: frozenset[str] = frozenset(),
        registration_admission: RelevanceAdmission | None = None,
    ) -> dict[str, Any]:
        """One queue snapshot for a source check; this is not source evidence."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            task = self._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            resolved = None
            if registration is not None:
                resolved = resolve_registration(
                    conn, task, registration, disabled_ids=disabled_ids, require_revision=False,
                    admission=registration_admission,
                )
            closeout = closeout_request(conn, task_id)
            return {
                "task_id": task.id, "repo": task.repo, "source": task.source,
                "origin_ref": task.origin_ref, "generation": task.generation,
                "revision": _revision(conn, task_id), "status": task.status,
                "custody": _custody(conn, task),
                "closeout": closeout_detail(closeout),
                **({
                    "registration_id": registration.registration_id,
                    "registration_revision": registration_revision(conn),
                    **({"effective_revision": resolved.effective_revision}
                       if resolved is not None and resolved.effective_revision is not None else {}),
                    **({"emitter_spec_revision": resolved.emitter_spec_revision}
                       if resolved is not None else {}),
                } if registration is not None else {}),
            }

    def reconcile_task_relevance(
        self, task_id: str, *, request_id: str, scope: RelevanceScope | RelevanceRegistration,
        expected_generation: int, expected_revision: int, verdict: str,
        reason: str, observation_ref: str, reporter: str, now: float | None = None,
        disabled_ids: frozenset[str] = frozenset(),
        on_transition: Callable[[CompletionOutcome], None] | None = None,
        registration_admission: RelevanceAdmission | None = None,
        evaluation_token: str | None = None,
    ) -> dict[str, Any]:
        """Accept a source judgment and ownership decision in one write.

        ``observation_ref`` identifies the caller's backing-object observation;
        it does not authenticate it or turn an incomplete listing into closure.
        A closeout receipt is intent plus a linked original-owner wake when
        available, not transport acceptance or native/workspace cleanup.
        """
        if not isinstance(scope, (RelevanceScope, RelevanceRegistration)):
            raise TaskError("relevance needs a coordinator-resolved source scope")
        for name, value in asdict(scope).items():
            if name != "revision" and not (name == "effective_revision" and value is None):
                _text(value, name)
        for name, value in (
            ("request_id", request_id), ("reason", reason),
            ("observation_ref", observation_ref), ("reporter", reporter),
        ):
            _text(value, name)
        if verdict not in {"needed", "obsolete", "unknown"}:
            raise TaskError("relevance verdict must be needed, obsolete or unknown")
        if any(type(value) is not int or value < 0 for value in (
            expected_generation, expected_revision,
        )):
            raise TaskError("relevance generation and revision must be nonnegative integers")
        scope_fields = asdict(scope)
        if isinstance(scope, RelevanceRegistration) and scope.effective_revision is None:
            scope_fields.pop("effective_revision")
        request = {
            "scope": scope_fields, "generation": expected_generation,
            "revision": expected_revision, "verdict": verdict, "reason": reason,
            "observation_ref": observation_ref, "reporter": reporter,
        }
        encoded = _encoded(request)
        ts = self._now(now)
        if not math.isfinite(ts):
            raise TaskError("relevance acceptance requires a finite timestamp")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT * FROM task_relevance_requests WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            ).fetchone()
            if previous is not None:
                if previous["request_json"] != encoded:
                    raise TaskError("relevance request key conflicts with its original request")
                return cast("dict[str, Any]", json.loads(previous["result_json"]))
            if evaluation_token is not None:
                if not isinstance(scope, RelevanceRegistration) or not conn.execute(
                    "SELECT 1 FROM task_relevance_evaluations WHERE task_id=?"
                    " AND registration_id=? AND token=? AND expires_at>?"
                    " AND status='pending'",
                    (task_id, scope.registration_id, evaluation_token, ts),
                ).fetchone():
                    raise TaskError("periodic relevance delivery ownership changed")
            task = self._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            if isinstance(scope, RelevanceRegistration):
                scope = RelevanceScope(*resolve_registration(
                    conn, task, scope, disabled_ids=disabled_ids, require_revision=True,
                    admission=registration_admission,
                ).scope)
            if task.kind != "finite" or (task.repo, task.source, task.origin_ref) != (
                scope.repo, scope.source, scope.origin_ref,
            ):
                raise TaskError("relevance source scope does not match this finite task")
            if task.generation != expected_generation or _revision(conn, task_id) != expected_revision:
                raise TaskError("relevance task revision changed; refresh the source check")
            closeout = closeout_request(conn, task_id)
            custody = _custody(conn, task)
            steer_id = closeout["steer_id"] if closeout is not None else None
            decision = "unchanged"
            to_status = task.status
            # An obsolete submission, or a pending closeout whose worker and
            # acquisition are gone (startup exhausted and released), settles
            # directly; there is no worker left to wake.
            settled_closeout = closeout is not None and (
                closeout["acknowledgement"] is not None or not custody
            )
            if closeout is not None and not (
                settled_closeout and verdict == "obsolete" and task.hold_reason is None
            ):
                decision = "closeout_pending"
            elif verdict == "obsolete" and task.status not in {*Status.TERMINAL, Status.DEAD_LETTER}:
                if (settled_closeout or not custody) and task.hold_reason is None:
                    decision, to_status = "abandoned", Status.ABANDONED
                    conn.execute(
                        "UPDATE tasks SET status=?, owner=NULL, lease_expires_at=NULL,"
                        " activity=NULL, activity_updated_at=?, updated_at=? WHERE id=?",
                        (to_status, ts, ts, task_id),
                    )
                else:
                    decision = "closeout_pending"
                    fields = {
                        "task_relevance": {
                            "request_id": request_id, "verdict": verdict, "reason": reason,
                            "observation_ref": observation_ref, "generation": task.generation,
                            "instruction": (
                                "Stop pursuing the original goal. Preserve existing work and obligations; "
                                "close out through the original session and managed-worktree owners. "
                                "Task abandonment alone is not session or workspace cleanup."
                            ),
                        },
                    }
                    inserted = conn.execute(
                        "INSERT INTO task_steer (task_id, ts, fields, sender)"
                        " VALUES (?, ?, ?, ?)",
                        (task_id, ts, _encoded(fields), reporter),
                    )
                    steer_id = inserted.lastrowid
                    target, binding = capture_target(conn, task)
                    conn.execute(
                        "INSERT INTO task_relevance_closeouts"
                        " (task_id, request_id, generation, owner, owner_session_id, steer_id,"
                        " created_at, resource_binding) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (task_id, request_id, task.generation, (
                            target.owner or (
                                target.completed_by if target.status == Status.SUBMITTED else None
                            )
                        ),
                         target.owner_session_id, steer_id, ts, _encoded(binding)),
                    )
            wake = None
            if decision == "closeout_pending":
                from .queue_relevance_delivery import enqueue_closeout
                pending = closeout_request(conn, task_id)
                assert pending is not None
                wake = enqueue_closeout(self, conn, task, pending, ts=ts)
            retained_wake = conn.execute(
                "SELECT w.id, w.status FROM wake_outbox w"
                " JOIN task_steer s ON s.wake_operation_id=w.id WHERE s.id=?", (steer_id,),
            ).fetchone() if steer_id is not None else None
            result = {
                "task_id": task_id, "request_id": request_id, "decision": decision,
                "status": to_status, "generation": task.generation, "custody": custody,
                "owner": task.owner, "owner_session_id": task.owner_session_id,
                "steer_id": steer_id, "accepted_at": ts,
                "wake_operation_id": retained_wake["id"] if retained_wake else None,
                "delivery": (
                    "held" if steer_id is not None and task.hold_reason is not None
                    else retained_wake["status"] if retained_wake else
                    "pending_owner_closeout" if steer_id is not None
                    else "not_requested"
                ),
            }
            self._audit(
                conn, task_id, ts=ts, from_status=task.status, to_status=to_status,
                worker=reporter, note=f"relevance {verdict}: {decision} ({request_id})",
            )
            conn.execute(
                "INSERT INTO task_relevance_requests VALUES (?, ?, ?, ?)",
                (task_id, request_id, encoded, _encoded(result)),
            )
            settled = self._fetch(conn, task_id) if decision == "abandoned" else None
            transition = CompletionOutcome(settled, "task.abandoned") if settled is not None else None
            conn.execute("COMMIT")
        self._notify_owned_transition()
        if wake is not None:
            self._notify_wake()
        if transition is not None and on_transition is not None:
            on_transition(transition)
        return result

    def relevance_receipt(
        self, task_id: str, request_id: str, *, registration_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Original acceptance plus current intent/steer observation, never cleanup proof."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT request_json, result_json FROM task_relevance_requests WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            ).fetchone()
            if row is None:
                return None
            if registration_id is not None and (
                json.loads(row["request_json"])["scope"].get("registration_id") != registration_id
            ):
                raise TaskError("relevance receipt belongs to a different registration")
            result = json.loads(row["result_json"])
            steer = conn.execute(
                "SELECT taken, taken_at FROM task_steer WHERE id=?", (result["steer_id"],),
            ).fetchone()
            closeout = closeout_request(conn, task_id)
            wake = conn.execute(
                "SELECT w.id, w.status, w.execution_send_state, w.last_error FROM wake_outbox w"
                " JOIN task_steer s ON s.wake_operation_id=w.id WHERE s.id=?",
                (closeout["steer_id"],),
            ).fetchone() if closeout else None
            return {
                "acceptance": result, "closeout": closeout_detail(closeout),
                "steer_consumed_at": steer["taken_at"] if steer and steer["taken"] else None,
                "wake": dict(wake) if wake else None,
            }
