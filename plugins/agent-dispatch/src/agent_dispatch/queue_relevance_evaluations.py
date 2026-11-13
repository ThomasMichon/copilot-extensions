"""Periodic relevance due work in the existing coordinator queue."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
import sqlite3
import uuid

from .queue_records import TaskError
from .queue_common import Task
from .queue_relevance import _revision
from .queue_relevance_registration import (
    RelevanceAdmission, RelevanceRegistration, _held, registration_revision, resolve_registration,
)
from .relevance_evaluation import PeriodicRelevancePolicy


class QueueRelevanceEvaluationsMixin:
    @staticmethod
    def _migrate_relevance_evaluations(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_relevance_evaluations ("
            "task_id TEXT NOT NULL, generation INTEGER NOT NULL, registration_id TEXT NOT NULL,"
            "status TEXT NOT NULL, attempts INTEGER NOT NULL, next_due REAL NOT NULL,"
            "token TEXT, expires_at REAL, snapshot_json TEXT NOT NULL, report_json TEXT,"
            "last_error TEXT, PRIMARY KEY(task_id, generation, registration_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_relevance_evaluation_cursors ("
            "registration_id TEXT PRIMARY KEY, task_id TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_relevance_evaluation_owner ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1), registration_id TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relevance_evaluations_due"
            " ON task_relevance_evaluations(registration_id, status, next_due)"
        )

    def rotate_relevance_evaluators(self, registration_ids: list[str]) -> list[str]:
        """Bound registrar traversal and keep later registrations from starvation."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT registration_id FROM task_relevance_evaluation_owner WHERE singleton=1"
            ).fetchone()
            after = row["registration_id"] if row is not None else ""
            ordered = sorted(registration_ids)
            rotated = [rid for rid in ordered if rid > after] + [
                rid for rid in ordered if rid <= after
            ]
            selected = rotated[:25]
            if selected:
                conn.execute(
                    "INSERT INTO task_relevance_evaluation_owner VALUES (1, ?)"
                    " ON CONFLICT(singleton) DO UPDATE SET registration_id=excluded.registration_id",
                    (selected[-1],),
                )
            return selected

    def claim_relevance_evaluation(
        self, selector: RelevanceRegistration, policy: PeriodicRelevancePolicy, *,
        admission: RelevanceAdmission, disabled_ids: frozenset[str] = frozenset(),
        lease_seconds: float, now: float | None = None,
    ) -> dict | None:
        ts = self._now(now)
        if not math.isfinite(ts) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise TaskError("periodic relevance needs finite time and a positive delivery lease")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            effective = admission(conn, selector)
            reg = effective.registrations.get(selector.registration_id)
            if (
                reg is None or reg["kind"] != "evaluator" or reg["status"] != "active"
                or (reg["machine"], reg["env"]) != (selector.machine, selector.env)
                or PeriodicRelevancePolicy.from_spec(reg["spec"]) != policy
            ):
                raise TaskError("periodic relevance registration changed")
            if _held(reg, disabled_ids, effective.predecessor_override_ids):
                return None
            # Continue an evaluation admitted while unbound, even when a claim
            # won during its check. Refresh judgment, never replay a stale goal.
            retries = conn.execute(
                "SELECT * FROM task_relevance_evaluations WHERE registration_id=?"
                " AND status='pending' AND (report_json IS NOT NULL OR attempts>0) AND next_due<=?"
                " AND (token IS NULL OR expires_at<=?) ORDER BY next_due, task_id LIMIT ?",
                (selector.registration_id, ts, ts, policy.batch_size),
            ).fetchall()
            for row in retries:
                task = self._fetch(conn, row["task_id"])
                if task is not None and any(
                    item["kind"] == "emitter"
                    and (item["spec"].get("source") or "emitter", item["spec"].get("id"))
                    == (task.source, task.origin_ref)
                    and _held(item, disabled_ids, effective.predecessor_override_ids)
                    for item in effective.registrations.values()
                ):
                    continue
                if row["attempts"] >= policy.max_attempts:
                    conn.execute(
                        "UPDATE task_relevance_evaluations SET status='failed',"
                        " last_error='periodic relevance retry budget exhausted'"
                        " WHERE task_id=? AND generation=? AND registration_id=?",
                        (row["task_id"], row["generation"], selector.registration_id),
                    )
                    continue
                entry = dict(row)
                if entry["report_json"] is None:
                    prior = json.loads(entry["snapshot_json"])["task"]
                    if task is None or (
                        task.repo, task.source, task.origin_ref, task.evaluator_ref,
                    ) != tuple(prior[key] for key in ("repo", "source", "origin_ref", "evaluator_ref")):
                        conn.execute(
                            "UPDATE task_relevance_evaluations SET status='failed',"
                            " last_error='task source scope changed' WHERE task_id=?"
                            " AND generation=? AND registration_id=?",
                            (row["task_id"], row["generation"], selector.registration_id),
                        )
                        continue
                    if task.status in ("completed", "abandoned", "dead_letter"):
                        conn.execute(
                            "UPDATE task_relevance_evaluations SET status='finished'"
                            " WHERE task_id=? AND generation=? AND registration_id=?",
                            (task.id, row["generation"], selector.registration_id),
                        )
                        continue
                    try:
                        resolve_registration(
                            conn, task, selector, disabled_ids=disabled_ids,
                            require_revision=False, admission=lambda *_args: effective,
                        )
                    except TaskError:
                        continue
                    entry["snapshot_json"] = self._relevance_evaluation_snapshot(
                        conn, task, reg, effective.revision,
                    )
                # A saved report is recovered unchanged before any fresh check.
                return self._lease_relevance_evaluation(conn, entry, ts, lease_seconds)
            conn.execute(
                "UPDATE task_relevance_evaluations SET status='failed',"
                " last_error='periodic relevance retry budget exhausted'"
                " WHERE registration_id=? AND status='pending' AND attempts>=?"
                " AND token IS NOT NULL AND expires_at<=?",
                (selector.registration_id, policy.max_attempts, ts),
            )
            cursor = conn.execute(
                "SELECT task_id FROM task_relevance_evaluation_cursors WHERE registration_id=?",
                (selector.registration_id,),
            ).fetchone()
            after = cursor["task_id"] if cursor else ""
            rows = conn.execute(
                "SELECT t.id FROM tasks t LEFT JOIN task_relevance_evaluations e"
                " ON e.task_id=t.id AND e.generation=t.generation AND e.registration_id=?"
                " WHERE t.id>? AND t.kind='finite' AND t.status IN ('proposed','queued')"
                " AND t.owner IS NULL AND t.created_at<=? AND t.evaluator_ref=?"
                " AND (? OR t.repo=?)"
                " AND NOT EXISTS (SELECT 1 FROM task_relevance_closeouts c WHERE c.task_id=t.id)"
                " AND (e.task_id IS NULL OR (e.status='pending' AND e.next_due<=?"
                " AND (e.token IS NULL OR e.expires_at<=?)))"
                " ORDER BY t.id LIMIT ?",
                (selector.registration_id, after, ts - policy.min_age_seconds,
                 reg["spec"]["evaluator_ref"], bool(reg["spec"].get("all_repos")),
                 reg["spec"].get("repo"), ts, ts, policy.batch_size),
            ).fetchall()
            conn.execute(
                "INSERT INTO task_relevance_evaluation_cursors VALUES (?, ?)"
                " ON CONFLICT(registration_id) DO UPDATE SET task_id=excluded.task_id",
                (selector.registration_id, rows[-1]["id"] if rows else ""),
            )
            for candidate in rows:
                task = self._fetch(conn, candidate["id"])
                try:
                    resolve_registration(
                        conn, task, selector, disabled_ids=disabled_ids,
                        require_revision=False, admission=lambda *_args: effective,
                    )
                except TaskError:
                    continue
                row = conn.execute(
                    "SELECT * FROM task_relevance_evaluations"
                    " WHERE task_id=? AND generation=? AND registration_id=?",
                    (task.id, task.generation, selector.registration_id),
                ).fetchone()
                entry = dict(row) if row is not None else {
                    "task_id": task.id, "generation": task.generation,
                    "registration_id": selector.registration_id, "status": "pending",
                    "attempts": 0, "next_due": ts, "report_json": None, "last_error": None,
                }
                if entry["report_json"] is None:
                    entry["snapshot_json"] = self._relevance_evaluation_snapshot(
                        conn, task, reg, effective.revision,
                    )
                return self._lease_relevance_evaluation(conn, entry, ts, lease_seconds)
        return None

    @staticmethod
    def _relevance_evaluation_snapshot(
        conn: sqlite3.Connection, task: Task, reg: dict, effective_revision: str,
    ) -> str:
        return json.dumps({
            "task": asdict(task), "task_id": task.id, "generation": task.generation,
            "registration_spec": reg["spec"], "revision": _revision(conn, task.id),
            "registration_revision": registration_revision(conn),
            "effective_revision": effective_revision,
        }, allow_nan=False)

    @staticmethod
    def _lease_relevance_evaluation(
        conn: sqlite3.Connection, entry: dict, ts: float, lease_seconds: float,
    ) -> dict:
        entry.update(token=uuid.uuid4().hex, expires_at=ts + lease_seconds)
        entry["attempts"] += 1
        columns = (
            "task_id", "generation", "registration_id", "status", "attempts", "next_due",
            "token", "expires_at", "snapshot_json", "report_json", "last_error",
        )
        conn.execute(
            f"INSERT OR REPLACE INTO task_relevance_evaluations ({','.join(columns)})"
            f" VALUES ({','.join('?' for _ in columns)})",
            tuple(entry.get(key) for key in columns),
        )
        return entry

    def save_relevance_evaluation(
        self, entry: dict, report: dict, *, now: float | None = None,
    ) -> None:
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE task_relevance_evaluations SET report_json=? WHERE task_id=?"
                " AND generation=? AND registration_id=? AND token=? AND expires_at>?",
                (json.dumps(report, allow_nan=False), entry["task_id"], entry["generation"],
                 entry["registration_id"], entry["token"], self._now(now)),
            )
            if changed.rowcount != 1:
                raise TaskError("periodic relevance delivery ownership changed")

    def finish_relevance_evaluation(
        self, entry: dict, policy: PeriodicRelevancePolicy, *,
        verdict: str | None, error: str | None = None,
        preserve_report: bool = False, now: float | None = None,
    ) -> None:
        ts = self._now(now)
        retry = verdict != "needed"
        attempts = entry["attempts"] if retry else 0
        exhausted = retry and attempts >= policy.max_attempts
        delay = min(policy.interval_seconds, policy.retry_base * 2 ** max(0, attempts - 1))
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE task_relevance_evaluations SET status=?, attempts=?, next_due=?,"
                " token=NULL, expires_at=NULL, last_error=?,"
                " report_json=CASE WHEN ? THEN report_json ELSE NULL END"
                " WHERE task_id=? AND generation=? AND registration_id=? AND token=?",
                ("finished" if verdict == "obsolete" else "failed" if exhausted else "pending", attempts,
                 ts + (delay if retry else policy.interval_seconds), error, preserve_report,
                 entry["task_id"], entry["generation"], entry["registration_id"], entry["token"]),
            )
            if changed.rowcount != 1:
                raise TaskError("periodic relevance delivery ownership changed")

    def relevance_evaluations(self, task_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM task_relevance_evaluations WHERE task_id=?"
                " ORDER BY generation, registration_id", (task_id,),
            )]

    def retry_relevance_evaluation(
        self, task_id: str, *, registration_id: str, generation: int,
        now: float | None = None,
    ) -> None:
        """Explicit owner retry after failure; retain any unresolved judgment."""
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE task_relevance_evaluations SET status='pending', attempts=0,"
                " next_due=?, token=NULL, expires_at=NULL, last_error=NULL"
                " WHERE task_id=? AND generation=? AND registration_id=? AND status='failed'",
                (self._now(now), task_id, generation, registration_id),
            )
            if changed.rowcount != 1:
                raise TaskError("periodic relevance retry requires the failed evaluation episode")
