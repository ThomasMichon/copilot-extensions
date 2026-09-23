"""``TaskQueue`` mixin: claim, inbox, list/search, and reservation lookups.

Extracted from :mod:`agent_dispatch.queue` as the final queue core slice after
storage/creation and lifecycle/wake extraction: this band is the read/query
surface plus the claim-time selection logic that consumes the stored task rows.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on the storage mixin for
``self._canonical_repo()``, ``self._canonical_selector_tokens()``,
``self._fetch()``, and ``self._audit()`` and is not usable standalone.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence

from .queue_common import (
    ClaimOutcome,
    Task,
    _CLAIM_REJECTION_EVENT_LIMIT,
    _MAX_AFFINITY,
    _TASK_BULK_SELECT,
    machine_matches,
    worker_id_for,
)
from .queue_records import SpawnReservation, Status


class QueueClaimQueriesMixin:
    """Claim, inbox, query, and reservation lookup methods for ``TaskQueue``."""

    def claim_one(
        self,
        worker_id: str,
        capabilities: Iterable[str] = (),
        *,
        repo: str | None = None,
        machine: str | None = None,
        worktree: str | None = None,
        task_id: str | None = None,
        now: float | None = None,
        lease_seconds: int | None = None,
        evaluation: bool = False,
        _with_outcome: bool = False,
    ) -> Task | ClaimOutcome | None:
        """Atomically lease the best eligible ``queued`` task, or ``None``."""
        repo = self._canonical_repo(repo)
        ts = self._now(now)
        caps = set(self._canonical_selector_tokens(capabilities))
        full_caps = set(caps)
        if machine:
            full_caps.add(f"machine:{machine}")
        if worktree:
            full_caps.add(f"worktree:{worktree}")
        if repo:
            full_caps.add(f"repo:{repo}")
        if lease_seconds is not None:
            lease = lease_seconds
        elif evaluation:
            lease = self.eval_lease_seconds
        else:
            lease = self.lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if task_id is not None:
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
                    "WHERE id = ? AND status = ? AND not_before <= ? AND hold_reason IS NULL",
                    (task_id, Status.QUEUED, ts),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
                    "WHERE status = ? AND not_before <= ? AND hold_reason IS NULL"
                    " ORDER BY created_at ASC",
                    (Status.QUEUED, ts),
                ).fetchall()
            chosen: sqlite3.Row | None = None
            best_affinity = -1
            producer_rejections: list[dict[str, object]] = []
            for row in rows:
                if repo is not None and row["repo"] != repo:
                    continue
                rejection = self._claim_fence_rejection(conn, row)
                if rejection is not None:
                    if "_fingerprint" not in rejection:
                        rejection["_fingerprint"] = hashlib.sha256(
                            json.dumps(
                                {
                                    "task_id": row["id"],
                                    "repo": row["repo"],
                                    "source": row["source"],
                                    "labels": row["labels"],
                                    "producer_fence": row["producer_fence"],
                                    "producer_request_hash": row["producer_request_hash"],
                                    "reason": rejection["reason"],
                                },
                                ensure_ascii=True,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest()
                        rejection["fingerprint"] = str(rejection["_fingerprint"])[:16]
                    if len(
                        producer_rejections
                    ) < _CLAIM_REJECTION_EVENT_LIMIT and self._record_claim_rejection(
                        conn, rejection, ts=ts
                    ):
                        rejection.pop("_fingerprint")
                        producer_rejections.append(rejection)
                    continue
                requires = set(
                    self._canonical_selector_tokens(
                        json.loads(row["requires"] or "[]"), strict=False
                    )
                )
                if not requires.issubset(full_caps):
                    continue
                excludes = set(
                    self._canonical_selector_tokens(
                        json.loads(row["excludes"] or "[]"), strict=False
                    )
                )
                if excludes & full_caps:
                    continue
                if not machine_matches(row["target_machine"], machine):
                    continue
                if row["target_worktree"] is not None and row["target_worktree"] != worktree:
                    continue
                score = self._affinity_score(json.loads(row["affinity"] or "{}"), worker_id, caps)
                if score > best_affinity:
                    best_affinity, chosen = score, row
                    if score == _MAX_AFFINITY:
                        break
            if chosen is None:
                conn.execute("COMMIT")
                outcome = ClaimOutcome(
                    task=None,
                    producer_rejections=producer_rejections,
                )
                return outcome if _with_outcome else None
            conn.execute(
                "UPDATE tasks SET status = ?, owner = ?, claimed_at = ?, updated_at = ?,"
                " lease_expires_at = ?, last_seen_at = ?, generation = generation + 1,"
                " owner_session_id = NULL, last_liveness = NULL,"
                " attempts = attempts + 1 WHERE id = ? AND status = ?",
                (Status.CLAIMED, worker_id, ts, ts, ts + lease, ts, chosen["id"], Status.QUEUED),
            )
            self._audit(
                conn,
                chosen["id"],
                ts=ts,
                from_status=Status.QUEUED,
                to_status=Status.CLAIMED,
                worker=worker_id,
                note="claim",
            )
            task = self._fetch(conn, chosen["id"])
            conn.execute("COMMIT")
        outcome = ClaimOutcome(
            task=task,
            producer_rejections=producer_rejections,
        )
        if task is not None:
            self._notify_owned_transition()
        return outcome if _with_outcome else task

    def claim_outcome(self, *args: object, **kwargs: object) -> ClaimOutcome:
        """Claim a task and return newly recorded producer rejection events."""
        kwargs["_with_outcome"] = True
        result = self.claim_one(*args, **kwargs)  # type: ignore[arg-type]
        assert isinstance(result, ClaimOutcome)
        return result

    def mine(
        self, machine: str, worktree: str, *, repo: str | None = None
    ) -> dict[str, list[Task]]:
        """Return an agent's inbox: tasks ``assigned`` to it and ``owned`` by it."""
        repo = self._canonical_repo(repo)
        owner = worker_id_for(machine, worktree)
        repo_clause = " AND repo = ?" if repo is not None else ""
        repo_param: tuple = (repo,) if repo is not None else ()
        with self._connect() as conn:
            assigned_rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks WHERE status = ? AND ("  # noqa: S608
                "  target_worktree = ?"
                "  OR (target_machine = ? COLLATE NOCASE AND target_worktree IS NULL)"
                ")" + repo_clause + " ORDER BY created_at ASC",
                (Status.QUEUED, worktree, machine, *repo_param),
            ).fetchall()
            owned_rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                "WHERE owner = ? AND status IN (?, ?, ?)"
                + repo_clause
                + " ORDER BY created_at ASC",
                (
                    owner,
                    Status.CLAIMED,
                    Status.STARTED,
                    Status.SUSPENDED,
                    *repo_param,
                ),
            ).fetchall()
        return {
            "assigned": [Task._from_row(r) for r in assigned_rows],
            "owned": [Task._from_row(r) for r in owned_rows],
        }

    @staticmethod
    def _affinity_score(affinity: dict[str, str], worker_id: str, caps: set[str]) -> int:
        """Rank a queued task for a worker: exact agent match > capability hint > any."""
        if not affinity:
            return 0
        pref_agent = affinity.get("agent")
        if pref_agent in (worker_id, "same") and pref_agent is not None:
            return _MAX_AFFINITY
        pref_cap = affinity.get("capability")
        if pref_cap is not None and pref_cap in caps:
            return 1
        return 0

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            return self._fetch(conn, task_id)

    def has_pending_wakes(self) -> bool:
        """Return whether a pending wake exists, including a delayed retry."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM wake_outbox WHERE status = 'pending' LIMIT 1"
            ).fetchone()
        return row is not None

    def list(
        self,
        *,
        repo: str | None = None,
        status: str | Sequence[str] | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        evaluator_ref: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        exclusive_key: str | None = None,
        limit: int = 200,
    ) -> list[Task]:
        """List tasks, optionally filtered. Newest first."""
        repo = self._canonical_repo(repo)
        clauses: list[str] = []
        params: list[object] = []
        if repo is not None:
            clauses.append("repo = ?")
            params.append(repo)
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                clauses.append(f"status IN ({placeholders})")
                params.extend(statuses)
        if target_machine is not None:
            clauses.append("target_machine = ? COLLATE NOCASE")
            params.append(target_machine)
        if target_repo is not None:
            clauses.append("target_repo = ?")
            params.append(target_repo)
        if evaluator_ref is not None:
            if evaluator_ref:
                clauses.append("evaluator_ref = ?")
                params.append(evaluator_ref)
            else:
                clauses.append("evaluator_ref IS NULL")
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if origin_ref is not None:
            clauses.append("origin_ref = ?")
            params.append(origin_ref)
        if exclusive_key is not None:
            clauses.append("exclusive_key = ?")
            params.append(exclusive_key)
        if label is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each("
                "CASE WHEN json_valid(tasks.labels) THEN tasks.labels ELSE '[]' END"
                ") "
                "WHERE json_each.value = ?)"
            )
            params.append(label)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def find(self, text: str, *, repo: str | None = None, limit: int = 50) -> list[Task]:
        """Substring search over title/prompt."""
        repo = self._canonical_repo(repo)
        like = f"%{text}%"
        repo_clause = " AND repo = ?" if repo is not None else ""
        repo_param: tuple = (repo,) if repo is not None else ()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                "WHERE (title LIKE ? OR prompt LIKE ?)"
                + repo_clause
                + " ORDER BY created_at DESC LIMIT ?",
                (like, like, *repo_param, limit),
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def sweep(self, *, repo: str | None = None, limit: int = 500) -> list[Task]:
        """Return the dedup corpus: every non-abandoned task, newest first."""
        return self.list(repo=repo, status=self.SWEEP_STATES, limit=limit)

    def events(self, task_id: str) -> list[dict[str, object]]:
        """Return the append-only audit trail for a task, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, from_status, to_status, worker, note FROM task_events "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def progress_log(self, task_id: str) -> list[dict[str, object]]:
        """Return the accumulated append-only progress log for a task."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, phase, summary, detail, worker FROM task_progress "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_reservation(self, key: str) -> SpawnReservation | None:
        """Return one reservation by key, or ``None``."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
        return SpawnReservation._from_row(row) if row else None

    def latest_reservation(self, task_id: str) -> SpawnReservation | None:
        """Return the highest-attempt reservation for a task, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return SpawnReservation._from_row(row) if row else None

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | Sequence[str] | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        limit: int = 200,
    ) -> list[SpawnReservation]:
        """List spawn reservations, newest first, optionally filtered."""
        repo = self._canonical_repo(repo)
        clauses: list[str] = []
        params: list[object] = []
        if task_id is not None:
            clauses.append("r.task_id = ?")
            params.append(task_id)
        if state is not None:
            states = [state] if isinstance(state, str) else list(state)
            clauses.append(f"r.state IN ({','.join('?' * len(states))})")
            params.extend(states)
        join_tasks = repo is not None or label is not None or resume_requested is not None
        if repo is not None:
            clauses.append("t.repo = ?")
            params.append(repo)
        if label is not None:
            clauses.append("EXISTS (SELECT 1 FROM json_each(t.labels) WHERE value = ?)")
            params.append(label)
        if conclusion_state is not None:
            clauses.append("r.conclusion_state = ?")
            params.append(conclusion_state)
        if resume_requested is not None:
            clauses.append("t.resume_requested = ?")
            params.append(1 if resume_requested else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.* FROM spawn_reservations r "
                f"{'JOIN tasks t ON t.id = r.task_id ' if join_tasks else ''}"
                f"{where} ORDER BY r.reserved_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [SpawnReservation._from_row(r) for r in rows]
