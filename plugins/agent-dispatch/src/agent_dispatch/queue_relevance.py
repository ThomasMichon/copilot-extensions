"""Atomic source-relevance decisions; transport and owner closeout stay separate.

This receiver is internal to the trusted coordinator. No HTTP, CLI, emitter or
periodic caller is enabled here. Callers must resolve source scope and judge the
backing object; queue history decides whether abandonment is resource-free.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import sqlite3

from .queue_common import Task
from .queue_records import Status, TaskError


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
    return conn.execute(
        "SELECT * FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
    ).fetchone()


def require_relevant_goal(conn: sqlite3.Connection, task_id: str) -> None:
    if closeout_request(conn, task_id) is not None:
        raise TaskError("task relevance requires owner closeout, not restarting its original goal")


def _revision(conn: sqlite3.Connection, task_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id=?", (task_id,),
    ).fetchone()[0]


def _custody(conn: sqlite3.Connection, task: Task) -> list[str]:
    """Conservative absence proof; an empty current owner is not never-claimed."""
    retained = []
    if (
        task.status not in {Status.PROPOSED, Status.QUEUED}
        or task.generation or task.attempts or task.owner or task.owner_session_id
        or task.claimed_at is not None or task.started_at is not None
    ):
        retained.append("claim_or_start")
    if task.target_worktree or task.affinity.get("worktree") or any(
        token.startswith("worktree:") for token in task.requires
    ):
        retained.append("workspace_reference")
    if conn.execute(
        "SELECT 1 FROM task_events WHERE task_id=? AND ("
        "from_status IN ('claimed','started','suspended','submitted')"
        " OR to_status IN ('claimed','started','suspended','submitted')"
        " OR task_relationship IS NOT NULL) LIMIT 1", (task.id,),
    ).fetchone():
        retained.append("assignment_history")
    for table in (
        "task_attachments", "spawn_reservations", "task_pool_assignments",
        "acquisition_receipts", "execution_send_custody", "run_waiters",
        "wake_outbox", "run_waiter_wakes", "resource_reservations",
    ):
        # Fixed owner tables, including historical/pending preparation, not caller SQL.
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE task_id=? LIMIT 1", (task.id,),
        ).fetchone():
            retained.append(table)
    return retained


class QueueRelevanceMixin:
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

    def relevance_snapshot(self, task_id: str) -> dict:
        """One queue snapshot for a source check; this is not source evidence."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            task = self._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
            closeout = closeout_request(conn, task_id)
            return {
                "task_id": task.id, "repo": task.repo, "source": task.source,
                "origin_ref": task.origin_ref, "generation": task.generation,
                "revision": _revision(conn, task_id), "status": task.status,
                "custody": _custody(conn, task),
                "closeout": dict(closeout) if closeout is not None else None,
            }

    def reconcile_task_relevance(
        self, task_id: str, *, request_id: str, scope: RelevanceScope,
        expected_generation: int, expected_revision: int, verdict: str,
        reason: str, observation_ref: str, reporter: str, now: float | None = None,
    ) -> dict:
        """Accept a source judgment and ownership decision in one write.

        ``observation_ref`` identifies the caller's backing-object observation;
        it does not authenticate it or turn an incomplete listing into closure.
        A closeout receipt is durable intent/steering, not wake delivery or
        native/workspace cleanup. Those effects require the existing owners.
        """
        if not isinstance(scope, RelevanceScope):
            raise TaskError("relevance needs a coordinator-resolved source scope")
        for name, value in asdict(scope).items():
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
        request = {
            "scope": asdict(scope), "generation": expected_generation,
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
                return json.loads(previous["result_json"])
            task = self._fetch(conn, task_id)
            if task is None:
                raise TaskError(f"no such task {task_id!r}")
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
            if closeout is not None:
                decision = "closeout_pending"
            elif verdict == "obsolete" and task.status not in {*Status.TERMINAL, Status.DEAD_LETTER}:
                if not custody and task.hold_reason is None:
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
                    conn.execute(
                        "INSERT INTO task_relevance_closeouts"
                        " (task_id, request_id, generation, owner, owner_session_id, steer_id, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (task_id, request_id, task.generation, task.owner,
                         task.owner_session_id, steer_id, ts),
                    )
            result = {
                "task_id": task_id, "request_id": request_id, "decision": decision,
                "status": to_status, "generation": task.generation, "custody": custody,
                "owner": task.owner, "owner_session_id": task.owner_session_id,
                "steer_id": steer_id, "accepted_at": ts,
                "delivery": (
                    "held" if steer_id is not None and task.hold_reason is not None
                    else "pending_owner_closeout" if steer_id is not None
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
            conn.execute("COMMIT")
        self._notify_owned_transition()
        return result

    def relevance_receipt(self, task_id: str, request_id: str) -> dict | None:
        """Original acceptance plus current intent/steer observation, never cleanup proof."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT result_json FROM task_relevance_requests WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            ).fetchone()
            if row is None:
                return None
            result = json.loads(row["result_json"])
            steer = conn.execute(
                "SELECT taken, taken_at FROM task_steer WHERE id=?", (result["steer_id"],),
            ).fetchone()
            closeout = closeout_request(conn, task_id)
            return {
                "acceptance": result, "closeout": dict(closeout) if closeout else None,
                "steer_consumed_at": steer["taken_at"] if steer and steer["taken"] else None,
            }
