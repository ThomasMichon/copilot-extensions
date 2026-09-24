"""SQLite database -- schema, migrations, and query helpers."""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("agent-bridge")

SCHEMA_VERSION = 22

# Post-base ``sessions`` columns ensured idempotently on every init, independent
# of ``schema_version``. Version-gated ``ALTER TABLE ... ADD COLUMN`` migrations
# (``if from_version < N: ...``) are skipped for a DB already stamped at/after N,
# so a table that never received a column (created/copied at a higher version, or
# a partial migration) would stay permanently missing it -- which broke the
# elevated daemon's DB and failed every ``usage_update`` (dotfiles #815). All are
# nullable so ``ADD COLUMN`` is always safe.
_SESSIONS_ENSURE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("caller_id", "TEXT"),
    ("context_size", "INTEGER"),
    ("context_used", "INTEGER"),
    ("usage_model", "TEXT"),
    ("last_usage_at", "REAL"),
    ("predecessor_id", "TEXT"),
    ("successor_id", "TEXT"),
    ("handoff_at", "REAL"),
    ("background_recovery_enabled", "INTEGER NOT NULL DEFAULT 1"),
)

# A live interactive session is kept alive by the extension's 30s heartbeat
# (HEARTBEAT_MS in extension.mjs). A row whose ``updated_at`` is older than this
# window has missed several heartbeats and is treated as dead when *resolving* a
# worktree handle to its current session -- so a handoff's stale predecessor row
# is never picked over the live successor. Generous (4 missed beats) to tolerate
# a briefly-busy event loop.
LIVE_SESSION_STALE_SECONDS = 120.0

#: Grace window after which a dead live-session row (``expired`` or terminal
#: ``taken-over``) is purged from the registry, bounding the "graveyard" of
#: dead registrations that ``list`` and consumers (Neuron Forge) would
#: otherwise surface indefinitely (#3144). Comfortably longer than the lease so
#: a just-ended row survives a while for debugging (via ``include_dead``) before
#: it self-cleans; consumers don't see it regardless (the default list hides
#: dead rows). ``wedged`` rows are never purged -- their process is alive.
LIVE_SESSION_PURGE_SECONDS = 3600.0


def local_pid_alive(pid: Any) -> bool | None:
    """Best-effort *local* liveness probe for a live-session's CLI ``pid``.

    Returns ``True`` if a process with that pid exists, ``False`` if it provably
    does not, and ``None`` when liveness cannot be determined -- on Windows
    (no safe signal-probe) or for a missing/invalid pid -- in which case the
    caller must fall back to the heartbeat lease.

    The ``live_sessions`` registry is **per-machine**: the bundled extension
    registers only with its *local* bridge (#1485), and cross-machine reach is
    bridge-to-bridge forwarding (the peer records the row on *its* host), so a
    stored pid is always a pid on *this* machine -- a local probe is valid.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":  # no safe os.kill(pid, 0) probe on Windows; lease-only
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user -- still alive
    except OSError:
        return None
    return True


def live_session_is_fresh(
    row: dict[str, Any],
    now: float,
    stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
) -> bool:
    """Is a live-session registration *fresh* (its heartbeat lease still valid)?

    A registration is fresh when it is still ``status=='live'`` **and** its
    ``updated_at`` heartbeat falls within the lease window. ``updated_at`` is
    refreshed on every register/heartbeat and on event-ingest, so a fresh row
    means the CLI process is still calling home -- the load-bearing liveness
    signal for the ownership guard (#2879) and the inbox freshness lease
    (#2906). Note a turn-derived ``liveness=='stalled'`` row is still *fresh*:
    the process heartbeats even while a turn is silent. A missed-heartbeat row,
    a ``wedged`` row (process alive but the extension stopped heartbeating,
    #3145), or one demoted to ``status!='live'`` by the reaper / a take-over,
    all read as *not* fresh.
    """
    if (row.get("status") or "live") != "live":
        return False
    updated = row.get("updated_at")
    if not isinstance(updated, (int, float)):
        return False
    return updated >= now - stale_seconds

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
    context_size INTEGER,
    context_used INTEGER,
    usage_model TEXT,
    last_usage_at REAL,
    predecessor_id TEXT,
    successor_id TEXT,
    handoff_at REAL,
    background_recovery_enabled INTEGER NOT NULL DEFAULT 1,
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

CREATE TABLE IF NOT EXISTS delivery_cursor_invalidations (
    caller_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    prior_last_acked_id INTEGER NOT NULL,
    prior_head_id INTEGER NOT NULL,
    prior_continuity_id TEXT,
    current_continuity_id TEXT,
    invalidated_at REAL NOT NULL,
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
    driven_by TEXT,
    status TEXT NOT NULL DEFAULT 'live',
    turn_state TEXT,
    last_activity_at REAL,
    latest_progress TEXT,
    cli_mode INTEGER NOT NULL DEFAULT 0,
    venue TEXT,
    registered_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS live_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    reply_to TEXT,
    kind TEXT NOT NULL DEFAULT 'prompt',
    delivery TEXT NOT NULL DEFAULT 'queue',
    idempotency_key TEXT,
    created_at REAL NOT NULL,
    delivered_at REAL
);

CREATE TABLE IF NOT EXISTS worktree_ownership (
    worktree_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    reserved_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cli_mode_reservations (
    worktree_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    claimed_by_session_id TEXT,
    venue TEXT
);

CREATE TABLE IF NOT EXISTS pending_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    caller_id TEXT,
    prompt TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id, event_id);
CREATE INDEX IF NOT EXISTS idx_live_sessions_worktree ON live_sessions(worktree_id);
CREATE INDEX IF NOT EXISTS idx_live_messages_pending
    ON live_messages(session_id, delivered_at, id);
CREATE INDEX IF NOT EXISTS idx_pending_prompts_session
    ON pending_prompts(session_id, id);
"""
class _CoreMixin:
    """Connection, schema-bootstrap, and low-level query helpers."""

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

    @property
    def db_path(self) -> Path:
        """The resolved on-disk path of this database file."""
        return self._db_path

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
        # Read-and-clear: surface a recorded writer error exactly ONCE, then
        # reset the latch. Previously the latch stayed set until a full
        # writer-thread restart, so a single failed batch re-raised on EVERY
        # subsequent flush()/read and 500'd the whole daemon indefinitely --
        # status/resume/cursor/turns for healthy AND wedged sessions alike
        # (dotfiles #1282). Self-healing surfacing keeps a genuine failure
        # visible without wedging unrelated reads.
        with self._writer_state_lock:
            exc = self._writer_error
            self._writer_error = None
        if exc is not None:
            raise RuntimeError("event writer failed") from exc

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
            except sqlite3.IntegrityError:
                # `executemany` is atomic: ONE bad row (an orphan event whose
                # session_id has no `sessions` parent -- a lifecycle/write-
                # ordering race -- or a duplicate (session_id, event_id)) would
                # otherwise drop every GOOD event in the batch AND latch
                # `_writer_error`, which then 500s every later flush()/read for
                # ALL sessions, wedging the daemon (dotfiles #1282). Fall back
                # to row-by-row inserts, skipping only the offenders so good
                # events still land and no writer error is recorded.
                conn.rollback()
                self._write_event_batch_rowwise(conn, batch)
            except Exception:
                conn.rollback()
                raise

    def _write_event_batch_rowwise(
        self, conn: sqlite3.Connection, batch: list[_EventWriteItem | object]
    ) -> None:
        """Insert events one-by-one, skipping constraint-violating rows.

        Called only after a batch ``executemany`` hit an ``IntegrityError``,
        with ``self._write_lock`` already held by the caller. Orphan-session
        events (no matching ``sessions`` row) and duplicate
        ``(session_id, event_id)`` rows are dropped with a warning rather than
        raising -- an unattachable event is unreadable anyway, and dropping it
        is far cheaper than wedging every read. Non-integrity errors still
        propagate (a genuine write failure is worth surfacing).
        """
        skipped: list[str] = []
        for item in batch:
            try:
                conn.execute(
                    "INSERT INTO events "
                    "(session_id, event_id, event_type, data_json, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    item,
                )
            except sqlite3.IntegrityError:
                sid = item[0] if isinstance(item, (tuple, list)) and item else "?"
                skipped.append(str(sid))
        conn.commit()
        if skipped:
            distinct = sorted(set(skipped))
            log.warning(
                "Dropped %d event(s) with no/duplicate session parent -- kept "
                "the rest of the batch (orphan session_id(s): %s) (dotfiles "
                "#1282)",
                len(skipped), ", ".join(distinct[:5]),
            )

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
