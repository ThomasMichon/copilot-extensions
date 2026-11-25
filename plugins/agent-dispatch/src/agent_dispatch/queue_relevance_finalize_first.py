"""Supervisor finalize-first settlement for obsolete tasks whose worker is not hot.

Before a supervisor starts, retries or resumes a worker only to close out an
obsolete task, it may fence the task and run a real ``agent-worktrees finalize``
on the task's own managed worktree. An accepted finalization is authoritative
evidence there is nothing left for a worker to clean up, so the task is
abandoned without a wake and its reservation is released through normal
cleanup. A refused, unavailable or failed finalization only lifts the fence:
the worker is started or resumed and performs the closeout itself.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .queue_common import CompletionOutcome, Task, _task_transition_spec
from .queue_lifecycle_host import single_task
from .queue_records import SpawnState, Status, TaskError

FINISH_ACTION = "supervisor-finalize-first/v1"
FINALIZE_RUNNER = "agent-worktrees finalize/v1"
FENCE_SECONDS = 600.0
REFUSAL_COOLDOWN_SECONDS = 3600.0
CANDIDATE_LIMIT = 4

_RESERVATION_FIELDS = (
    "key", "attempt", "state", "reserved_by", "worktree", "worktree_ownership",
    "execution_machine", "allocation_project", "worktree_path", "driver",
)


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_relevance_finalize_first ("
        "task_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, token TEXT NOT NULL,"
        " state TEXT NOT NULL CHECK (state IN ('fenced','refused')),"
        " phase TEXT NOT NULL, reservation_key TEXT NOT NULL, supervisor_id TEXT NOT NULL,"
        " updated_at REAL NOT NULL, expires_at REAL NOT NULL, detail TEXT)"
    )


def finalize_fenced(conn: sqlite3.Connection, task_id: str, now: float | None = None) -> bool:
    """A live fence keeps any session from starting against the worktree mid-finalize."""
    return conn.execute(
        "SELECT 1 FROM task_relevance_finalize_first"
        " WHERE task_id=? AND state='fenced' AND expires_at>?",
        (task_id, time.time() if now is None else now),
    ).fetchone() is not None


_CANDIDATES = (
    "SELECT t.id AS task_id, t.status, t.generation, t.owner, t.owner_session_id,"
    " " + ", ".join(f"s.{name} AS r_{name}" for name in _RESERVATION_FIELDS) +
    " FROM tasks t JOIN task_relevance_closeouts c ON c.task_id=t.id"
    " JOIN spawn_reservations s ON s.task_id=t.id"
    " AND s.attempt=(SELECT MAX(attempt) FROM spawn_reservations WHERE task_id=t.id)"
    " WHERE c.acknowledgement IS NULL AND t.hold_reason IS NULL AND t.kind='finite'"
    " AND s.reserved_by=? AND s.worktree IS NOT NULL AND s.worktree_ownership='created'"
    " AND s.allocation_project IS NOT NULL AND s.worktree_path IS NOT NULL"
    " AND s.cooling_token IS NULL"
    # Unstarted: an ownerless claim whose acquisition failed or is unknown.
    " AND ((t.status=? AND t.owner IS NULL AND t.owner_session_id IS NULL AND s.state IN (?,?)"
    " AND NOT EXISTS (SELECT 1 FROM task_attachments a"
    " WHERE a.task_id=t.id AND a.detached_at IS NULL))"
    # Cold: a suspended owner whose closeout wake was delivered and not re-armed.
    " OR (t.status=? AND s.state=? AND t.resume_requested=0"
    " AND NOT EXISTS (SELECT 1 FROM wake_outbox w WHERE w.task_id=t.id"
    " AND (w.status IN ('pending','delivering')"
    " OR w.execution_send_state IN ('pending','unknown')))))"
    " AND NOT EXISTS (SELECT 1 FROM task_relevance_finalize_first f WHERE f.task_id=t.id"
    " AND f.expires_at>? AND (f.state='fenced' OR f.generation=t.generation))"
)


def _params(supervisor_id: str) -> tuple[object, ...]:
    # Fence and cooldown expiry use the wall clock, like the admission gate.
    return (
        supervisor_id, Status.CLAIMED, SpawnState.RESERVING, SpawnState.SPAWNED,
        Status.SUSPENDED, SpawnState.COLD, time.time(),
    )


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"], "status": row["status"], "generation": row["generation"],
        "owner": row["owner"], "owner_session_id": row["owner_session_id"],
        "phase": "unstarted" if row["status"] == Status.CLAIMED else "cold",
        "reservation": {name: row[f"r_{name}"] for name in _RESERVATION_FIELDS},
    }


class QueueRelevanceFinalizeFirstMixin:
    if TYPE_CHECKING:
        _connect: Callable[[], sqlite3.Connection]
        _now: Callable[[float | None], float]
        _fetch: Callable[[sqlite3.Connection, str], Task | None]
        _audit: Callable[..., None]
        _transition: Callable[..., Task | tuple[Task, bool]]
        submit_steer: Callable[..., Task]

    def relevance_finalize_candidates(
        self, supervisor_id: str, *, limit: int = CANDIDATE_LIMIT, now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Obsolete tasks this supervisor's own reservation could settle by finalizing first."""
        with self._connect() as conn:
            rows = conn.execute(
                _CANDIDATES + " ORDER BY t.updated_at ASC LIMIT ?",
                (*_params(supervisor_id), max(1, min(limit, CANDIDATE_LIMIT))),
            ).fetchall()
        return [_candidate(row) for row in rows]

    def fence_relevance_finalize(
        self, task_id: str, *, supervisor_id: str, generation: int, reservation_key: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Fence start/retry/resume for this exact incarnation before finalizing."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                _CANDIDATES + " AND t.id=?", (*_params(supervisor_id), task_id),
            ).fetchone()
            if row is None or row["generation"] != generation or row["r_key"] != reservation_key:
                conn.execute("COMMIT")
                raise TaskError("relevance finalize-first candidate changed; not fenced")
            candidate = _candidate(row)
            token = uuid.uuid4().hex
            conn.execute(
                "INSERT OR REPLACE INTO task_relevance_finalize_first"
                " (task_id, generation, token, state, phase, reservation_key, supervisor_id,"
                " updated_at, expires_at, detail) VALUES (?, ?, ?, 'fenced', ?, ?, ?, ?, ?, NULL)",
                (task_id, generation, token, candidate["phase"], reservation_key, supervisor_id,
                 ts, time.time() + FENCE_SECONDS),
            )
            conn.execute("COMMIT")
        return {"token": token, **candidate}

    def _finalize_fence(self, conn: sqlite3.Connection, task_id: str, token: str) -> sqlite3.Row:
        fence: sqlite3.Row | None = conn.execute(
            "SELECT * FROM task_relevance_finalize_first WHERE task_id=? AND token=? AND state='fenced'",
            (task_id, token),
        ).fetchone()
        if fence is None:
            raise TaskError("relevance finalize-first fence is not held by this request")
        return fence

    def settle_relevance_finalize(
        self, task_id: str, *, token: str, evidence: dict[str, Any], now: float | None = None,
    ) -> CompletionOutcome:
        """Abandon on accepted finalization evidence for the fenced reservation's worktree."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN")
            fence = self._finalize_fence(conn, task_id, token)
            task = self._fetch(conn, task_id)
            reservation = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (fence["reservation_key"],),
            ).fetchone()
            conn.execute("COMMIT")
        if task is None or reservation is None or task.generation != fence["generation"]:
            raise TaskError("relevance finalize-first incarnation changed; custody retained")
        if (
            evidence.get("runner") != FINALIZE_RUNNER or evidence.get("status") != "finalized"
            or evidence.get("worktree_id") != reservation["worktree"]
            or evidence.get("project") != reservation["allocation_project"]
        ):
            raise TaskError("relevance finalize-first evidence does not match the fenced worktree")
        receipt = {
            "task_id": task_id, "finish_action": FINISH_ACTION, "at": ts,
            "supervisor_id": fence["supervisor_id"], "reservation_key": fence["reservation_key"],
            "phase": fence["phase"], "finalization": evidence, "process_retirement": False,
            "task_attachment": {
                "generation": task.generation, "owner_session_id": task.owner_session_id,
            },
        }

        def retain(conn: sqlite3.Connection) -> None:
            self._finalize_fence(conn, task_id, token)
            if conn.execute(
                "UPDATE task_relevance_closeouts SET acknowledgement=?"
                " WHERE task_id=? AND acknowledgement IS NULL",
                (json.dumps(receipt, sort_keys=True, separators=(",", ":")), task_id),
            ).rowcount != 1:
                raise TaskError("relevance closeout was already acknowledged")
            conn.execute("DELETE FROM task_relevance_finalize_first WHERE task_id=?", (task_id,))

        allowed, to = _task_transition_spec("abandon")
        result = single_task(self._transition(
            task_id, allowed=allowed, to=to, worker_id=fence["supervisor_id"],
            require_owner=False, now=ts,
            note="source relevance settled by supervisor finalize-first",
            extra={"owner": None, "lease_expires_at": None},
            expected_generation=task.generation, expected_owner_session_id=task.owner_session_id,
            expected_status=task.status, reject_if_held=True, transaction_guard=retain,
            unaccepted_closeout=True, release_spawn=True,
            release_spawn_detail="relevance finalize-first settled",
        ))
        return CompletionOutcome(result, "task.abandoned")

    def release_relevance_finalize(
        self, task_id: str, *, token: str, detail: str, now: float | None = None,
    ) -> dict[str, Any]:
        """Lift the fence after refusal; a cold owner is re-armed to close out itself."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            fence = self._finalize_fence(conn, task_id, token)
            conn.execute(
                "UPDATE task_relevance_finalize_first SET state='refused', updated_at=?,"
                " expires_at=?, detail=? WHERE task_id=?",
                (ts, time.time() + REFUSAL_COOLDOWN_SECONDS, detail[:1024], task_id),
            )
            task = self._fetch(conn, task_id)
            status = task.status if task is not None else Status.ABANDONED
            self._audit(
                conn, task_id, ts=ts, from_status=status, to_status=status,
                worker=fence["supervisor_id"],
                note=f"relevance finalize-first refused; worker closeout continues: {detail[:256]}",
            )
            conn.execute("COMMIT")
        rearmed = None
        if fence["phase"] == "cold":
            # Suspended owners have no other automatic next move: resume them to close out.
            try:
                rearmed = self.submit_steer(
                    task_id, sender=fence["supervisor_id"], wake_requested=True,
                    expected_status=Status.SUSPENDED,
                    idempotency_key=f"relevance-finalize-first-rearm:{token}",
                    fields={"task_relevance": {
                        "rearm": True, "generation": fence["generation"],
                        "instruction": (
                            "The source of this task is obsolete. Resume only to close out: "
                            "preserve existing work and obligations, finalize the managed "
                            "worktree and abandon or submit the task."
                        ),
                    }},
                ).status
            except TaskError as exc:
                rearmed = f"refused: {exc}"[:256]
        return {"task_id": task_id, "state": "refused", "phase": fence["phase"], "rearm": rearmed}
