"""SQLite-backed leased task queue -- the agent-dispatch engine.

A single-writer, WAL-mode SQLite queue providing an **atomic leased claim** over
a set of *tasks*. This module is deliberately transport-free: it is a pure
library that the coordinator process wraps behind HTTP. Everything that must be
*correct under concurrency* lives here, patterned on a proven single-writer
leased-queue design.

Design notes
------------
* **Six-state model** (see :class:`Status`):
  ``proposed -> queued -> claimed -> started -> completed`` plus terminal
  ``abandoned``. ``proposed`` is never claimable; an internal lease-expiry
  transition returns a held task to ``queued`` (attempts++).
* **Capability-gated claim.** A task carries a hard ``requires`` set (capability
  tokens or an ``agent:<id>`` identity pin); a worker advertises a capability
  set at claim time. A task is claimable only when ``requires`` is a subset of
  the worker's capabilities. ``affinity`` is a soft preference that orders
  candidates but never excludes.
* **Cooperative claiming = redundancy.** ``claim_one`` takes a write lock
  (``BEGIN IMMEDIATE``) and re-checks ``status='queued'`` before committing, so
  N capable workers racing for one task yield exactly one winner. A dead worker's
  lease expires and any other capable worker reclaims it -- no leader election.
* **Additive migrations.** ``_migrate`` runs ``CREATE TABLE IF NOT EXISTS`` plus
  idempotent ``ALTER TABLE`` column adds, so an existing DB upgrades safely (a
  bare ``CREATE TABLE IF NOT EXISTS`` never upgrades an existing table).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .payload import PayloadStore, is_blob_ref

DEFAULT_LEASE_SECONDS = 15 * 60
#: Payloads whose UTF-8 size exceeds this are spilled to a content-addressed blob
#: instead of being stored inline in the row.
DEFAULT_BLOB_THRESHOLD = 4096
_BUSY_TIMEOUT_MS = 5000
_MAX_AFFINITY = 1000


def worker_id_for(machine: str, worktree: str) -> str:
    """The canonical agent identity: the ``machine/worktree`` composite.

    This pair is the only durable agent id the facility has; the coordinator
    stamps it as a task's ``owner`` on claim, and an agent finds its own work by
    querying with the same pair (see :meth:`TaskQueue.mine`).
    """
    return f"{machine}/{worktree}"


class Status:
    """The six task states (string constants, stored verbatim)."""

    PROPOSED = "proposed"
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTED = "started"
    COMPLETED = "completed"
    ABANDONED = "abandoned"

    #: States a worker actively holds (leased); recoverable on lease expiry.
    HELD = frozenset({CLAIMED, STARTED})
    #: Terminal states -- no further transitions.
    TERMINAL = frozenset({COMPLETED, ABANDONED})
    #: Non-terminal states from which an abandon (with permission) is allowed.
    ABANDONABLE = frozenset({PROPOSED, QUEUED, CLAIMED, STARTED})


class TaskError(RuntimeError):
    """Raised on an illegal state transition or a lease/ownership violation."""


@dataclass(frozen=True)
class Task:
    """A read-only snapshot of a task row."""

    id: str
    title: str
    prompt: str
    status: str
    requires: list[str] = field(default_factory=list)
    affinity: dict[str, str] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    payload_ref: str | None = None
    payload_inline: str | None = None
    target_machine: str | None = None
    target_worktree: str | None = None
    target_repo: str | None = None
    source: str | None = None
    origin_ref: str | None = None
    dedup_key: str | None = None
    owner: str | None = None
    attempts: int = 0
    not_before: float = 0.0
    lease_expires_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    claimed_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    result_ref: str | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> Task:
        return cls(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            status=row["status"],
            requires=json.loads(row["requires"] or "[]"),
            affinity=json.loads(row["affinity"] or "{}"),
            labels=json.loads(row["labels"] or "[]"),
            payload_ref=row["payload_ref"],
            payload_inline=row["payload_inline"],
            target_machine=row["target_machine"],
            target_worktree=row["target_worktree"],
            target_repo=row["target_repo"],
            source=row["source"],
            origin_ref=row["origin_ref"],
            dedup_key=row["dedup_key"],
            owner=row["owner"],
            attempts=row["attempts"],
            not_before=row["not_before"],
            lease_expires_at=row["lease_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claimed_at=row["claimed_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result_ref=row["result_ref"],
        )


# Column name -> DDL type, applied additively so existing DBs upgrade in place.
_COLUMNS: dict[str, str] = {
    "id": "TEXT PRIMARY KEY",
    "title": "TEXT NOT NULL DEFAULT ''",
    "prompt": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'queued'",
    "requires": "TEXT NOT NULL DEFAULT '[]'",
    "affinity": "TEXT NOT NULL DEFAULT '{}'",
    "labels": "TEXT NOT NULL DEFAULT '[]'",
    "payload_ref": "TEXT",
    "payload_inline": "TEXT",
    "target_machine": "TEXT",
    "target_worktree": "TEXT",
    "target_repo": "TEXT",
    "source": "TEXT",
    "origin_ref": "TEXT",
    "dedup_key": "TEXT",
    "owner": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "not_before": "REAL NOT NULL DEFAULT 0",
    "lease_expires_at": "REAL",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "claimed_at": "REAL",
    "started_at": "REAL",
    "completed_at": "REAL",
    "result_ref": "TEXT",
}


class TaskQueue:
    """A leased, capability-gated task queue over a SQLite database file.

    Instances are cheap; each operation opens its own short-lived connection so
    the queue is safe to share across threads (each thread gets its own
    connection). WAL mode + ``BEGIN IMMEDIATE`` on the write path give atomic
    claims without a process-wide lock.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        payload_dir: str | Path | None = None,
        blob_threshold: int = DEFAULT_BLOB_THRESHOLD,
    ):
        self.db_path = str(db_path)
        self.lease_seconds = lease_seconds
        self.blob_threshold = blob_threshold
        # Blobs live in a ``payloads/`` directory beside the queue DB unless the
        # caller overrides it (e.g. a shared blob volume).
        if payload_dir is None:
            payload_dir = Path(self.db_path).parent / "payloads"
        self.payloads = PayloadStore(payload_dir)
        self._migrate()

    # -- connection / schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY)")
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            for name, decl in _COLUMNS.items():
                if name == "id" or name in existing:
                    continue
                # name/decl are internal constants from _COLUMNS, never user input.
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedup "
                "ON tasks(dedup_key) WHERE dedup_key IS NOT NULL"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_events ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  from_status TEXT,"
                "  to_status TEXT,"
                "  worker TEXT,"
                "  note TEXT"
                ")"
            )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else now

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        task_id: str,
        *,
        ts: float,
        from_status: str | None,
        to_status: str,
        worker: str | None = None,
        note: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO task_events (task_id, ts, from_status, to_status, worker, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, ts, from_status, to_status, worker, note),
        )

    def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return Task._from_row(row) if row else None

    # -- payload -------------------------------------------------------------

    def _spill_payload(
        self, payload_ref: str | None, payload_inline: str | None
    ) -> tuple[str | None, str | None]:
        """Spill an oversized inline payload to a content-addressed blob.

        A caller-supplied ``payload_ref`` is always respected (the caller took
        control of storage). Otherwise, an inline payload larger than
        ``blob_threshold`` bytes is written to the blob store and replaced by its
        ``blob:<hash>`` ref, keeping the row (and every list/find result) small.
        """
        if payload_ref is not None or payload_inline is None:
            return payload_ref, payload_inline
        if len(payload_inline.encode("utf-8")) <= self.blob_threshold:
            return payload_ref, payload_inline
        return self.payloads.put(payload_inline), None

    def read_payload(self, task_or_id: Task | str) -> str | None:
        """Resolve a task's payload content (inline or blob), or ``None``.

        Returns the inline text when present, the blob content when
        ``payload_ref`` is a ``blob:`` ref, and ``None`` for an absent payload or
        an external/opaque ``payload_ref`` (e.g. ``pr/123``) the caller resolves
        itself.
        """
        task = self.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
        if task is None:
            raise TaskError(f"no such task: {task_or_id}")
        if task.payload_inline is not None:
            return task.payload_inline
        if is_blob_ref(task.payload_ref):
            return self.payloads.get(task.payload_ref)  # type: ignore[arg-type]
        return None

    # -- producers -----------------------------------------------------------

    def create(
        self,
        title: str,
        *,
        prompt: str = "",
        status: str = Status.QUEUED,
        requires: Sequence[str] | None = None,
        affinity: dict[str, str] | None = None,
        labels: Sequence[str] | None = None,
        payload_ref: str | None = None,
        payload_inline: str | None = None,
        target_machine: str | None = None,
        target_worktree: str | None = None,
        target_repo: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        dedup_key: str | None = None,
        not_before: float = 0.0,
        now: float | None = None,
    ) -> Task:
        """Insert a task (default status ``queued``; ``proposed`` for a draft).

        If ``dedup_key`` collides with an existing task, no new row is created
        and the *existing* task is returned (ideation-time duplicate guard).
        """
        if status not in (Status.QUEUED, Status.PROPOSED):
            raise TaskError(f"new task must be 'queued' or 'proposed', not {status!r}")
        payload_ref, payload_inline = self._spill_payload(payload_ref, payload_inline)
        ts = self._now(now)
        task_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if dedup_key is not None:
                existing = conn.execute(
                    "SELECT * FROM tasks WHERE dedup_key = ?", (dedup_key,)
                ).fetchone()
                if existing is not None:
                    conn.execute("COMMIT")
                    return Task._from_row(existing)
            conn.execute(
                "INSERT INTO tasks (id, title, prompt, status, requires, affinity, labels,"
                " payload_ref, payload_inline, target_machine, target_worktree, target_repo,"
                " source, origin_ref, dedup_key, not_before, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    title,
                    prompt,
                    status,
                    json.dumps(list(requires or [])),
                    json.dumps(dict(affinity or {})),
                    json.dumps(list(labels or [])),
                    payload_ref,
                    payload_inline,
                    target_machine,
                    target_worktree,
                    target_repo,
                    source,
                    origin_ref,
                    dedup_key,
                    not_before,
                    ts,
                    ts,
                ),
            )
            self._audit(conn, task_id, ts=ts, from_status=None, to_status=status, note="create")
            conn.execute("COMMIT")
        return self.get(task_id)  # type: ignore[return-value]

    def propose(self, title: str, **kwargs: object) -> Task:
        """Create a task in the un-claimable ``proposed`` state."""
        kwargs["status"] = Status.PROPOSED
        return self.create(title, **kwargs)  # type: ignore[arg-type]

    def approve(self, task_id: str, *, now: float | None = None) -> Task:
        """Move a ``proposed`` task to ``queued`` (makes it claimable)."""
        return self._transition(
            task_id, allowed={Status.PROPOSED}, to=Status.QUEUED, now=now, note="approve"
        )

    # -- consumer / lease ----------------------------------------------------

    def claim_one(
        self,
        worker_id: str,
        capabilities: Iterable[str] = (),
        *,
        machine: str | None = None,
        worktree: str | None = None,
        task_id: str | None = None,
        now: float | None = None,
        lease_seconds: int | None = None,
    ) -> Task | None:
        """Atomically lease the best eligible ``queued`` task, or ``None``.

        Eligible = ``status='queued'``, ``not_before <= now``, every token in the
        task's ``requires`` present in ``capabilities``, and — the **targeting
        gate** — the task's ``target_machine`` / ``target_worktree`` are unset or
        match the claiming agent's ``machine`` / ``worktree``. So an agent only
        claims work that is unassigned *or* assigned to it. A claimer that leaves
        ``machine`` / ``worktree`` unset can therefore only take *untargeted*
        tasks. The winning row is flipped to ``claimed`` under a write lock, so
        concurrent callers never double-claim.

        If ``task_id`` is given, only that task is considered (a spawned worker
        deterministically claiming *its* task) — still subject to the same gates.

        ``worker_id`` is stamped as the task ``owner``; in the facility it is the
        canonical ``machine/worktree`` composite (see :func:`worker_id_for`).
        """
        ts = self._now(now)
        caps = set(capabilities)
        lease = self.lease_seconds if lease_seconds is None else lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if task_id is not None:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE id = ? AND status = ? AND not_before <= ?",
                    (task_id, Status.QUEUED, ts),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = ? AND not_before <= ?"
                    " ORDER BY created_at ASC",
                    (Status.QUEUED, ts),
                ).fetchall()
            chosen: sqlite3.Row | None = None
            best_affinity = -1
            for row in rows:
                requires = set(json.loads(row["requires"] or "[]"))
                if not requires.issubset(caps):
                    continue
                if row["target_machine"] is not None and row["target_machine"] != machine:
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
                return None
            conn.execute(
                "UPDATE tasks SET status = ?, owner = ?, claimed_at = ?, updated_at = ?,"
                " lease_expires_at = ?, attempts = attempts + 1 WHERE id = ? AND status = ?",
                (Status.CLAIMED, worker_id, ts, ts, ts + lease, chosen["id"], Status.QUEUED),
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
        return task

    def mine(self, machine: str, worktree: str) -> dict[str, list[Task]]:
        """Return an agent's inbox: tasks ``assigned`` to it and ``owned`` by it.

        - ``assigned``: ``queued`` tasks targeted specifically at this agent —
          ``target_worktree == worktree``, or a machine-wide assignment
          (``target_machine == machine`` with no worktree pin). Untargeted open
          tasks are *not* listed here (they belong to no one in particular).
        - ``owned``: non-terminal tasks this agent has claimed/started
          (``owner == machine/worktree``).
        """
        owner = worker_id_for(machine, worktree)
        with self._connect() as conn:
            assigned_rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? AND ("
                "  target_worktree = ?"
                "  OR (target_machine = ? AND target_worktree IS NULL)"
                ") ORDER BY created_at ASC",
                (Status.QUEUED, worktree, machine),
            ).fetchall()
            owned_rows = conn.execute(
                "SELECT * FROM tasks WHERE owner = ? AND status IN (?, ?) ORDER BY created_at ASC",
                (owner, Status.CLAIMED, Status.STARTED),
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

    def start(self, task_id: str, worker_id: str, *, now: float | None = None) -> Task:
        """Move a ``claimed`` task to ``started`` (owner must match)."""
        return self._transition(
            task_id,
            allowed={Status.CLAIMED},
            to=Status.STARTED,
            worker_id=worker_id,
            now=now,
            note="start",
            stamp="started_at",
        )

    def complete(
        self,
        task_id: str,
        worker_id: str,
        *,
        result_ref: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Move a ``started`` task to terminal ``completed`` (owner must match)."""
        return self._transition(
            task_id,
            allowed={Status.STARTED},
            to=Status.COMPLETED,
            worker_id=worker_id,
            now=now,
            note="complete",
            stamp="completed_at",
            extra={"result_ref": result_ref, "owner": None, "lease_expires_at": None},
        )

    def yield_task(
        self, task_id: str, worker_id: str, *, note: str | None = None, now: float | None = None
    ) -> Task:
        """Return a held (``claimed``/``started``) task to ``queued`` with updates.

        The recoverable-snag path (e.g. a merge conflict): the worker relinquishes
        the lease so the next scheduler cycle re-surfaces the task.
        """
        return self._transition(
            task_id,
            allowed=Status.HELD,
            to=Status.QUEUED,
            worker_id=worker_id,
            now=now,
            note=note or "yield",
            extra={"owner": None, "lease_expires_at": None, "claimed_at": None},
        )

    def abandon(
        self,
        task_id: str,
        *,
        worker_id: str | None = None,
        permitted: bool = False,
        reason: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Move a task to terminal ``abandoned`` -- requires ``permitted=True``.

        Abandonment is permission-gated (human/policy), never a unilateral agent
        action; callers pass ``permitted=True`` once that gate is satisfied.
        """
        if not permitted:
            raise TaskError("abandon requires permission (permitted=True)")
        return self._transition(
            task_id,
            allowed=Status.ABANDONABLE,
            to=Status.ABANDONED,
            worker_id=worker_id,
            require_owner=False,
            now=now,
            note=reason or "abandon",
            extra={"owner": None, "lease_expires_at": None},
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

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        """Return every held task whose lease has expired to ``queued``.

        This is the crash-recovery sweep: a worker that died mid-lease releases
        its task to any other capable worker. Returns the number recovered.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, status FROM tasks WHERE status IN (?, ?)"
                " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (Status.CLAIMED, Status.STARTED, ts),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE tasks SET status = ?, owner = NULL, lease_expires_at = NULL,"
                    " updated_at = ? WHERE id = ?",
                    (Status.QUEUED, ts, row["id"]),
                )
                self._audit(
                    conn,
                    row["id"],
                    ts=ts,
                    from_status=row["status"],
                    to_status=Status.QUEUED,
                    note="lease-expired",
                )
            conn.execute("COMMIT")
        return len(rows)

    def detach(self, task_id: str, *, now: float | None = None) -> Task:
        """Demote a hard worktree pin to a soft affinity (portability).

        A worktree-bound handoff becomes portable once local work is pushed: the
        ``worktree`` token moves out of ``requires`` and into ``affinity``.
        """
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

    # -- generic transition --------------------------------------------------

    def _transition(
        self,
        task_id: str,
        *,
        allowed: Iterable[str],
        to: str,
        worker_id: str | None = None,
        require_owner: bool = True,
        now: float | None = None,
        note: str | None = None,
        stamp: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> Task:
        ts = self._now(now)
        allowed_set = set(allowed)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in allowed_set:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot {note or to} a {task.status!r} task (allowed: {sorted(allowed_set)})"
                )
            if require_owner and worker_id is not None and task.owner not in (None, worker_id):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            sets = ["status = ?", "updated_at = ?"]
            params: list[object] = [to, ts]
            if stamp is not None:
                sets.append(f"{stamp} = ?")
                params.append(ts)
            for col, val in (extra or {}).items():
                sets.append(f"{col} = ?")
                params.append(val)
            params.append(task_id)
            # Column names are internal constants; values are bound parameters.
            conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)  # noqa: S608
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
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    # -- read helpers --------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            return self._fetch(conn, task_id)

    def list(
        self,
        *,
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        limit: int = 200,
    ) -> list[Task]:
        """List tasks, optionally filtered. Newest first."""
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if target_machine is not None:
            clauses.append("target_machine = ?")
            params.append(target_machine)
        if target_repo is not None:
            clauses.append("target_repo = ?")
            params.append(target_repo)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            # `where` is built from literal clause strings; values are bound.
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", params  # noqa: S608
            ).fetchall()
        tasks = [Task._from_row(r) for r in rows]
        if label is not None:
            tasks = [t for t in tasks if label in t.labels]
        return tasks

    def find(self, text: str, *, limit: int = 50) -> list[Task]:
        """Substring search over title/prompt (the pre-ideation dedup browse)."""
        like = f"%{text}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE title LIKE ? OR prompt LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def events(self, task_id: str) -> list[dict[str, object]]:
        """Return the append-only audit trail for a task, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, from_status, to_status, worker, note FROM task_events "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]
