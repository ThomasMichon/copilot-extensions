"""SQLite database -- schema, migrations, and query helpers."""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("agent-bridge")

SCHEMA_VERSION = 7
_EVENT_BATCH_MAX = 256
_EVENT_BATCH_WINDOW_SECS = 0.05
_EVENT_WRITE_SENTINEL = object()
_EventWriteItem = tuple[str, int, str, str, float]

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_name TEXT,
    caller_id TEXT,
    target_dir TEXT,
    target_type TEXT NOT NULL DEFAULT 'local',
    target_json TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    pid INTEGER,
    acp_session_id TEXT,
    config_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    response_text TEXT DEFAULT '',
    thought_text TEXT DEFAULT '',
    stop_reason TEXT,
    tool_calls_json TEXT DEFAULT '[]',
    started_at REAL,
    completed_at REAL,
    PRIMARY KEY (session_id, turn_index),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS events (
    session_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    timestamp REAL NOT NULL,
    PRIMARY KEY (session_id, event_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS delivery_cursors (
    caller_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    last_acked_id INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (caller_id, session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS live_sessions (
    session_id TEXT PRIMARY KEY,
    machine TEXT,
    cwd TEXT,
    worktree_id TEXT,
    repo TEXT,
    branch TEXT,
    pid INTEGER,
    role TEXT,
    status TEXT NOT NULL DEFAULT 'live',
    registered_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS live_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id, event_id);
CREATE INDEX IF NOT EXISTS idx_live_sessions_worktree ON live_sessions(worktree_id);
CREATE INDEX IF NOT EXISTS idx_live_messages_pending
    ON live_messages(session_id, delivered_at, id);
"""


class Database:
    """Thread-safe SQLite database for agent-bridge session persistence.

    Uses WAL mode for concurrent readers + single writer. All writes go
    through ``execute_write`` which holds a threading lock.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._event_write_q: queue.Queue[_EventWriteItem | object] = queue.Queue()
        self._writer_state_lock = threading.Lock()
        self._writer_thread: threading.Thread | None = None
        self._writer_error: BaseException | None = None
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL (vs the default FULL) skips the per-commit fsync of the WAL,
            # syncing only at checkpoints. Safe under WAL -- a power loss can lose
            # the last few commits but never corrupts the db. ~3x faster event
            # ingestion, which runs on the loop draining the ACP/SSH pipe (#99).
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def start_writer(self) -> None:
        """Start the background event writer thread if it is not already running."""
        with self._writer_state_lock:
            if self._writer_thread is not None and self._writer_thread.is_alive():
                return
            self._writer_error = None
            self._writer_thread = threading.Thread(
                target=self._event_writer_loop,
                name="agent-bridge-event-writer",
                daemon=True,
            )
            self._writer_thread.start()

    def stop_writer(self) -> None:
        """Flush queued events and stop the background event writer thread."""
        thread = self._writer_thread
        if thread is None or not thread.is_alive():
            if self._event_write_q.unfinished_tasks:
                self.flush()
            self._raise_writer_error()
            return

        self._event_write_q.put(_EVENT_WRITE_SENTINEL)
        self._event_write_q.join()
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("event writer thread did not stop")
        self._raise_writer_error()

    def flush(self) -> None:
        """Block until every previously queued event has been committed."""
        if threading.current_thread() is self._writer_thread:
            return
        self.start_writer()
        self._event_write_q.join()
        self._raise_writer_error()

    def close(self) -> None:
        """Flush pending event writes, stop the writer, and close this thread's connection."""
        self.stop_writer()
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _raise_writer_error(self) -> None:
        if self._writer_error is not None:
            raise RuntimeError("event writer failed") from self._writer_error

    def _record_writer_error(self, exc: BaseException) -> None:
        with self._writer_state_lock:
            self._writer_error = exc

    def _event_writer_loop(self) -> None:
        """Persist queued event writes in batches on a dedicated SQLite connection."""
        try:
            while True:
                item = self._event_write_q.get()
                if item is _EVENT_WRITE_SENTINEL:
                    self._event_write_q.task_done()
                    self._drain_event_queue_for_stop()
                    return

                batch = [item]
                deadline = time.monotonic() + _EVENT_BATCH_WINDOW_SECS
                while len(batch) < _EVENT_BATCH_MAX:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        item = self._event_write_q.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if item is _EVENT_WRITE_SENTINEL:
                        self._commit_event_batch(batch)
                        self._event_write_q.task_done()
                        self._drain_event_queue_for_stop()
                        return
                    batch.append(item)

                self._commit_event_batch(batch)
        finally:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                conn.close()
                self._local.conn = None

    def _drain_event_queue_for_stop(self) -> None:
        batch: list[_EventWriteItem | object] = []
        while True:
            try:
                item = self._event_write_q.get_nowait()
            except queue.Empty:
                break
            if item is _EVENT_WRITE_SENTINEL:
                self._event_write_q.task_done()
                continue
            batch.append(item)
            if len(batch) >= _EVENT_BATCH_MAX:
                self._commit_event_batch(batch)
                batch = []
        self._commit_event_batch(batch)

    def _commit_event_batch(self, batch: list[_EventWriteItem | object]) -> None:
        if not batch:
            return
        try:
            self._write_event_batch(batch)
        except Exception as exc:
            self._record_writer_error(exc)
            log.exception("Failed to persist %d queued event(s)", len(batch))
        finally:
            for _ in batch:
                self._event_write_q.task_done()

    def _write_event_batch(self, batch: list[_EventWriteItem | object]) -> None:
        conn = self._get_conn()
        with self._write_lock:
            try:
                conn.executemany(
                    "INSERT INTO events "
                    "(session_id, event_id, event_type, data_json, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _init_schema(self) -> None:
        """Create tables if they don't exist, run migrations."""
        conn = self._get_conn()
        with self._write_lock:
            conn.executescript(_SCHEMA_SQL)
            # Check/set schema version
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                conn.commit()
            else:
                current = row["version"]
                if current < SCHEMA_VERSION:
                    self._migrate(conn, current)

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Run schema migrations from from_version to SCHEMA_VERSION."""
        if from_version < 2:
            # v1 -> v2: add target_json column for full SpawnTarget persistence
            cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            if "target_json" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN target_json TEXT")
                log.info("Migration v1->v2: added target_json column to sessions")
            conn.execute(
                "UPDATE schema_version SET version=?", (2,)
            )
            conn.commit()
            log.info("Schema migrated to version 2")

        if from_version < 3:
            # v2 -> v3: make target_dir nullable (binstub agents have no cwd)
            conn.executescript("""
                PRAGMA foreign_keys = OFF;
                DROP TABLE IF EXISTS sessions_new;
                CREATE TABLE sessions_new (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    agent_name TEXT,
                    target_dir TEXT,
                    target_type TEXT NOT NULL DEFAULT 'local',
                    target_json TEXT,
                    status TEXT NOT NULL DEFAULT 'created',
                    pid INTEGER,
                    acp_session_id TEXT,
                    config_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                INSERT INTO sessions_new
                    (id, name, agent_name, target_dir, target_type,
                     target_json, status, pid, acp_session_id,
                     config_json, created_at, updated_at)
                SELECT id, name, agent_name, target_dir, target_type,
                       target_json, status, pid, acp_session_id,
                       config_json, created_at, updated_at
                FROM sessions;
                DROP TABLE sessions;
                ALTER TABLE sessions_new RENAME TO sessions;
                PRAGMA foreign_keys = ON;
            """)
            conn.execute(
                "UPDATE schema_version SET version=?", (3,)
            )
            conn.commit()
            log.info("Schema migrated to version 3")

        if from_version < 4:
            # v3 -> v4: add caller_id + context window usage columns
            cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            if "caller_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN caller_id TEXT")
                log.info("Migration v3->v4: added caller_id column to sessions")
            for col, col_type in [
                ("context_size", "INTEGER"),
                ("context_used", "INTEGER"),
                ("usage_model", "TEXT"),
                ("last_usage_at", "REAL"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type}")
                    log.info("Migration v3->v4: added %s column to sessions", col)
            conn.execute(
                "UPDATE schema_version SET version=?", (4,)
            )
            conn.commit()
            log.info("Schema migrated to version 4")

        if from_version < 5:
            # v4 -> v5: add per-caller delivery cursor table for the
            # delivery-acked shared read cursor (streaming resume).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS delivery_cursors (
                    caller_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    last_acked_id INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (caller_id, session_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            conn.execute(
                "UPDATE schema_version SET version=?", (5,)
            )
            conn.commit()
            log.info("Schema migrated to version 5: added delivery_cursors")

        if from_version < 6:
            # v5 -> v6: add live_sessions registry for extension-backed
            # interactive CLI sessions (registered by the bundled extension).
            # NOT bridge-owned, so no FK to sessions; liveness is heartbeat-based.
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS live_sessions (
                    session_id TEXT PRIMARY KEY,
                    machine TEXT,
                    cwd TEXT,
                    worktree_id TEXT,
                    repo TEXT,
                    branch TEXT,
                    pid INTEGER,
                    role TEXT,
                    status TEXT NOT NULL DEFAULT 'live',
                    registered_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_live_sessions_worktree
                    ON live_sessions(worktree_id);
            """)
            conn.execute("UPDATE schema_version SET version=?", (6,))
            conn.commit()
            log.info("Schema migrated to version 6: added live_sessions")

        if from_version < 7:
            # v6 -> v7: add live_messages delivery queue for posting a message
            # INTO a live interactive CLI session (Phase 2 write path). Own
            # autoincrement PK, NO FK to sessions (targets a live_sessions id).
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS live_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    delivered_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_live_messages_pending
                    ON live_messages(session_id, delivered_at, id);
            """)
            conn.execute("UPDATE schema_version SET version=?", (7,))
            conn.commit()
            log.info("Schema migrated to version 7: added live_messages")

    def execute_write(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a write query under the write lock."""
        conn = self._get_conn()
        with self._write_lock:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor

    def execute_read(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Execute a read query (no lock needed with WAL)."""
        conn = self._get_conn()
        return conn.execute(sql, params).fetchall()

    # -- Session CRUD --------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        name: str,
        agent_name: str | None,
        target_dir: str | None,
        target_type: str,
        status: str,
        now: float,
        config_json: str | None = None,
        target_json: str | None = None,
        caller_id: str | None = None,
    ) -> None:
        self.execute_write(
            "INSERT INTO sessions (id, name, agent_name, caller_id, target_dir, "
            "target_type, status, config_json, target_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, name, agent_name, caller_id, target_dir, target_type,
             status, config_json, target_json, now, now),
        )

    def update_session_status(
        self, session_id: str, status: str, now: float, pid: int | None = None
    ) -> None:
        if pid is not None:
            self.execute_write(
                "UPDATE sessions SET status=?, pid=?, updated_at=? WHERE id=?",
                (status, pid, now, session_id),
            )
        else:
            self.execute_write(
                "UPDATE sessions SET status=?, pid=NULL, updated_at=? WHERE id=?",
                (status, now, session_id),
            )

    def update_session_acp_id(self, session_id: str, acp_session_id: str) -> None:
        """Persist the ACP session ID for resume support."""
        self.execute_write(
            "UPDATE sessions SET acp_session_id=? WHERE id=?",
            (acp_session_id, session_id),
        )

    def update_session_target(
        self, session_id: str, target_json: str, target_dir: str | None = None,
    ) -> None:
        """Persist updated target (e.g. after spawn resolves worktree_id/cwd)."""
        self.execute_write(
            "UPDATE sessions SET target_json=?, target_dir=? WHERE id=?",
            (target_json, target_dir, session_id),
        )

    def update_session_usage(
        self,
        session_id: str,
        *,
        context_size: int | None = None,
        context_used: int | None = None,
        usage_model: str | None = None,
        now: float,
    ) -> None:
        """Persist the latest context window usage for a session."""
        self.execute_write(
            "UPDATE sessions SET context_size=?, context_used=?, "
            "usage_model=?, last_usage_at=?, updated_at=? WHERE id=?",
            (context_size, context_used, usage_model, now, now, session_id),
        )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self.execute_read("SELECT * FROM sessions WHERE id=?", (session_id,))
        if rows:
            return dict(rows[0])
        return None

    def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.execute_read(
                "SELECT * FROM sessions WHERE status=? ORDER BY updated_at DESC",
                (status,),
            )
        else:
            rows = self.execute_read(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            )
        return [dict(r) for r in rows]

    # -- Live interactive-session registry (extension-backed) ---------------
    # These sessions are NOT owned by the bridge: an interactive Copilot CLI
    # registers itself via the bundled extension so the bridge can represent
    # and (later) message it. Distinct from the `sessions` table (bridge-spawned
    # ACP sessions). Liveness is heartbeat-based: `updated_at` is refreshed on
    # each register/heartbeat, and stale rows are reaped rather than trusting a
    # clean deregister (an interactive session can die ungracefully).

    def register_live_session(
        self,
        session_id: str,
        *,
        machine: str | None,
        cwd: str | None,
        worktree_id: str | None,
        repo: str | None,
        branch: str | None,
        pid: int | None,
        role: str | None,
        now: float,
    ) -> None:
        """Insert or refresh a live interactive-session registration (upsert)."""
        self.execute_write(
            "INSERT INTO live_sessions (session_id, machine, cwd, worktree_id, "
            "repo, branch, pid, role, status, registered_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "machine=excluded.machine, cwd=excluded.cwd, "
            "worktree_id=excluded.worktree_id, repo=excluded.repo, "
            "branch=excluded.branch, pid=excluded.pid, role=excluded.role, "
            "status='live', updated_at=excluded.updated_at",
            (session_id, machine, cwd, worktree_id, repo, branch, pid, role,
             now, now),
        )

    def deregister_live_session(self, session_id: str) -> None:
        """Remove a live interactive-session registration and its message queue."""
        self.execute_write(
            "DELETE FROM live_sessions WHERE session_id=?", (session_id,)
        )
        self.execute_write(
            "DELETE FROM live_messages WHERE session_id=?", (session_id,)
        )

    def get_live_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM live_sessions WHERE session_id=?", (session_id,)
        )
        return dict(rows[0]) if rows else None

    def list_live_sessions(
        self, worktree_id: str | None = None
    ) -> list[dict[str, Any]]:
        if worktree_id:
            rows = self.execute_read(
                "SELECT * FROM live_sessions WHERE worktree_id=? "
                "ORDER BY updated_at DESC",
                (worktree_id,),
            )
        else:
            rows = self.execute_read(
                "SELECT * FROM live_sessions ORDER BY updated_at DESC"
            )
        return [dict(r) for r in rows]

    # -- Live-message delivery queue (Phase 2 write path) --------------------
    # Messages posted INTO a live interactive session. Durable (persisted so a
    # zero-downtime cutover doesn't drop an undelivered message); the extension
    # polls pending rows, calls session.send, then acks -- at-least-once, with
    # the ack (delivered_at) making a redelivery a no-op rather than a double
    # injection.

    def enqueue_live_message(
        self, session_id: str, sender: str, body: str, now: float
    ) -> int:
        """Enqueue a message for delivery into a live session; return its id."""
        cur = self.execute_write(
            "INSERT INTO live_messages (session_id, sender, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, sender, body, now),
        )
        return int(cur.lastrowid or 0)

    def list_pending_live_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Undelivered messages for a session, oldest-first (delivery order)."""
        rows = self.execute_read(
            "SELECT * FROM live_messages "
            "WHERE session_id=? AND delivered_at IS NULL ORDER BY id ASC",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def ack_live_messages(
        self, session_id: str, ids: list[int], now: float
    ) -> int:
        """Mark the given messages delivered; return how many rows changed.

        Scoped to ``session_id`` so a caller can only ack its own queue, and
        idempotent (already-delivered rows are left untouched by the
        ``delivered_at IS NULL`` guard), so a redelivered ack never errors.
        """
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = self.execute_write(
            f"UPDATE live_messages SET delivered_at=? "
            f"WHERE session_id=? AND delivered_at IS NULL AND id IN ({placeholders})",
            (now, session_id, *ids),
        )
        return cur.rowcount

    def delete_session(self, session_id: str) -> None:
        self.flush()
        with self._write_lock:
            conn = self._get_conn()
            # Clear every child table that has a FK to sessions BEFORE the
            # session row, or `PRAGMA foreign_keys=ON` rejects the parent delete
            # (FOREIGN KEY constraint failed). delivery_cursors was easy to miss
            # here -- omitting it left ENDED sessions undeletable, which crashed
            # _rehydrate's ENDED-cleanup on the next startup.
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            conn.execute(
                "DELETE FROM delivery_cursors WHERE session_id=?", (session_id,)
            )
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.commit()

    def delete_events(self, session_id: str) -> None:
        """Delete all persisted events for a session (keeps the session row).

        Used by the resync flow, which rebuilds the event log from the
        agent's authoritative load-time replay.
        """
        self.flush()
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            conn.commit()

    # -- Turn CRUD -----------------------------------------------------------

    def create_turn(
        self, session_id: str, turn_index: int, prompt: str, now: float
    ) -> None:
        self.execute_write(
            "INSERT INTO turns (session_id, turn_index, prompt, started_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, turn_index, prompt, now),
        )

    def update_turn(
        self,
        session_id: str,
        turn_index: int,
        *,
        response_text: str | None = None,
        thought_text: str | None = None,
        stop_reason: str | None = None,
        tool_calls_json: str | None = None,
        completed_at: float | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if response_text is not None:
            updates.append("response_text=?")
            params.append(response_text)
        if thought_text is not None:
            updates.append("thought_text=?")
            params.append(thought_text)
        if stop_reason is not None:
            updates.append("stop_reason=?")
            params.append(stop_reason)
        if tool_calls_json is not None:
            updates.append("tool_calls_json=?")
            params.append(tool_calls_json)
        if completed_at is not None:
            updates.append("completed_at=?")
            params.append(completed_at)
        if not updates:
            return
        params.extend([session_id, turn_index])
        self.execute_write(
            f"UPDATE turns SET {', '.join(updates)} "
            f"WHERE session_id=? AND turn_index=?",
            tuple(params),
        )

    def get_turns(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.execute_read(
            "SELECT * FROM turns WHERE session_id=? ORDER BY turn_index",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def get_turn(self, session_id: str, turn_index: int) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM turns WHERE session_id=? AND turn_index=?",
            (session_id, turn_index),
        )
        return dict(rows[0]) if rows else None

    # -- Event CRUD ----------------------------------------------------------

    def append_event(
        self,
        session_id: str,
        event_id: int,
        event_type: str,
        data: dict[str, Any],
        timestamp: float,
    ) -> None:
        self.start_writer()
        self._event_write_q.put((
            session_id, event_id, event_type, json.dumps(data), timestamp,
        ))

    def get_events(
        self, session_id: str, after: int = 0
    ) -> list[dict[str, Any]]:
        self.flush()
        rows = self.execute_read(
            "SELECT * FROM events WHERE session_id=? AND event_id>? ORDER BY event_id",
            (session_id, after),
        )
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            result.append(d)
        return result

    def get_max_event_id(self, session_id: str) -> int:
        self.flush()
        rows = self.execute_read(
            "SELECT MAX(event_id) as max_id FROM events WHERE session_id=?",
            (session_id,),
        )
        val = rows[0]["max_id"] if rows else None
        return val or 0

    def get_events_range(
        self, session_id: str, start_id: int, end_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Return events with start_id <= event_id <= end_id (inclusive).

        Used for random-access historical reads. Does not touch any
        delivery cursor. ``end_id=None`` means "to the latest event".
        """
        self.flush()
        if end_id is None:
            rows = self.execute_read(
                "SELECT * FROM events WHERE session_id=? AND event_id>=? "
                "ORDER BY event_id",
                (session_id, start_id),
            )
        else:
            rows = self.execute_read(
                "SELECT * FROM events WHERE session_id=? AND event_id>=? "
                "AND event_id<=? ORDER BY event_id",
                (session_id, start_id, end_id),
            )
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            result.append(d)
        return result

    # -- Delivery cursors ----------------------------------------------------

    def get_cursor(self, caller_id: str, session_id: str) -> int:
        """Return the last-acked event id for a caller on a session (0 if none)."""
        rows = self.execute_read(
            "SELECT last_acked_id FROM delivery_cursors "
            "WHERE caller_id=? AND session_id=?",
            (caller_id, session_id),
        )
        return rows[0]["last_acked_id"] if rows else 0

    def set_cursor(
        self, caller_id: str, session_id: str, last_acked_id: int, timestamp: float
    ) -> int:
        """Advance a caller's delivery cursor, monotonically.

        The stored value never regresses: a smaller or duplicate ack is
        ignored. Returns the cursor value in effect after the call.
        """
        with self._write_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT last_acked_id FROM delivery_cursors "
                "WHERE caller_id=? AND session_id=?",
                (caller_id, session_id),
            ).fetchone()
            current = row["last_acked_id"] if row else 0
            new_val = max(current, last_acked_id)
            conn.execute(
                "INSERT INTO delivery_cursors "
                "(caller_id, session_id, last_acked_id, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(caller_id, session_id) DO UPDATE SET "
                "last_acked_id=excluded.last_acked_id, updated_at=excluded.updated_at",
                (caller_id, session_id, new_val, timestamp),
            )
            conn.commit()
            return new_val

    def reset_delivery_cursors(self, session_id: str) -> None:
        """Drop all delivery cursors for a session (get_cursor -> 0 afterwards).

        Called when a session's event log is **rebuilt** (resync replaces the
        log with the agent's authoritative replay, renumbering event ids). The
        cursors are monotonic and would otherwise point past the rebuilt log --
        orphaning consumers (NF's "odd states"). Resetting them makes consumers
        re-read the rebuilt log from the start instead of silently stalling.
        """
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM delivery_cursors WHERE session_id=?", (session_id,)
            )
            conn.commit()

    # -- Garbage collection / maintenance ------------------------------------

    def gc_eligible_session_ids(
        self, statuses: list[str], cutoff_ts: float
    ) -> list[str]:
        """Return ids of sessions in ``statuses`` last updated before ``cutoff_ts``.

        Used by the GC sweep to find terminal/disconnected sessions whose
        relay metadata is past the retention window.
        """
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.execute_read(
            f"SELECT id FROM sessions WHERE status IN ({placeholders}) "
            "AND updated_at < ? ORDER BY updated_at",
            (*statuses, cutoff_ts),
        )
        return [r["id"] for r in rows]

    def db_size_info(self) -> dict[str, int]:
        """Return page/byte stats for the DB file (drives the VACUUM decision)."""
        conn = self._get_conn()
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        return {
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist,
            "total_bytes": page_size * page_count,
            "free_bytes": page_size * freelist,
        }

    def vacuum(self) -> None:
        """Checkpoint the WAL and VACUUM, returning freed pages to the OS.

        Runs under the write lock so no write interleaves. VACUUM rewrites
        the file; the scratch space it needs is ~the *live* content size
        (tiny when the DB is mostly freelist), so it succeeds even on a
        nearly-full disk. VACUUM cannot run inside an open transaction, so we
        commit any pending one first.
        """
        self.flush()
        conn = self._get_conn()
        with self._write_lock:
            if conn.in_transaction:
                conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
