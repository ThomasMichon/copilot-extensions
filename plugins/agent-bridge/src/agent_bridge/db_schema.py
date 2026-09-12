"""Database schema bootstrap and migrations."""

from __future__ import annotations

import sqlite3

from .db_core import SCHEMA_VERSION, _SCHEMA_SQL, _SESSIONS_ENSURE_COLUMNS, log


class _SchemaMixin:
    """Schema initialization and migration helpers."""

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
            # Safety net: ensure post-base columns exist regardless of
            # schema_version, so a DB stamped past a version-gated ADD COLUMN
            # migration (or a table created without a column) self-heals rather
            # than failing every write to that column (dotfiles #815).
            self._ensure_columns(conn)
            live_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(live_messages)"
                ).fetchall()
            }
            if "idempotency_key" not in live_columns:
                conn.execute(
                    "ALTER TABLE live_messages ADD COLUMN idempotency_key TEXT"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_live_messages_idempotency ON live_messages(idempotency_key)"
                " WHERE idempotency_key IS NOT NULL"
            )
            conn.commit()

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """Ensure canonical nullable session columns independently of schema stamps."""
        from .db_migrations import ensure_session_columns

        ensure_session_columns(conn, _SESSIONS_ENSURE_COLUMNS)

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

        if from_version < 8:
            # v7 -> v8: add reply_to to live_messages so a delivered message
            # carries the sender's routable address -- the receiving agent can
            # reply over the same bridge (agent-bridge send <reply_to>).
            cols = [
                r[1]
                for r in conn.execute("PRAGMA table_info(live_messages)").fetchall()
            ]
            if "reply_to" not in cols:
                conn.execute("ALTER TABLE live_messages ADD COLUMN reply_to TEXT")
            conn.execute("UPDATE schema_version SET version=?", (8,))
            conn.commit()
            log.info("Schema migrated to version 8: live_messages.reply_to")

        if from_version < 9:
            # v8 -> v9: add a typed `kind` to live_messages (D2). `prompt` (the
            # default) is a work directive; `notify`/`status-check` ask only for
            # a terse out-of-band ack and must not be treated as new work.
            cols = [
                r[1]
                for r in conn.execute("PRAGMA table_info(live_messages)").fetchall()
            ]
            if "kind" not in cols:
                conn.execute(
                    "ALTER TABLE live_messages ADD COLUMN kind TEXT "
                    "NOT NULL DEFAULT 'prompt'"
                )
            conn.execute("UPDATE schema_version SET version=?", (9,))
            conn.commit()
            log.info("Schema migrated to version 9: live_messages.kind")

        if from_version < 10:
            # v9 -> v10: add `driven_by` to live_sessions (D4). Names the agent
            # steering an agent-owned CLI session (the "driven by <agent>"
            # banner) so a human dropping in via Neuron Forge sees who's at the
            # wheel. NULL for an operator-launched session.
            cols = [
                r[1]
                for r in conn.execute("PRAGMA table_info(live_sessions)").fetchall()
            ]
            if "driven_by" not in cols:
                conn.execute("ALTER TABLE live_sessions ADD COLUMN driven_by TEXT")
            conn.execute("UPDATE schema_version SET version=?", (10,))
            conn.commit()
            log.info("Schema migrated to version 10: live_sessions.driven_by")

        if from_version < 11:
            # v10 -> v11: add `turn_state` + `last_activity_at` to live_sessions
            # (Phase 7 Channel A). Derived from the represented event tail so the
            # tracker sees running/idle/stalled turn-state, not just coarse
            # liveness. NULL until the session's extension pushes events.
            cols = [
                r[1]
                for r in conn.execute("PRAGMA table_info(live_sessions)").fetchall()
            ]
            if "turn_state" not in cols:
                conn.execute("ALTER TABLE live_sessions ADD COLUMN turn_state TEXT")
            if "last_activity_at" not in cols:
                conn.execute(
                    "ALTER TABLE live_sessions ADD COLUMN last_activity_at REAL"
                )
            conn.execute("UPDATE schema_version SET version=?", (11,))
            conn.commit()
            log.info(
                "Schema migrated to version 11: live_sessions.turn_state + "
                "last_activity_at"
            )

        if from_version < 12:
            # v11 -> v12: add `latest_progress` to live_sessions (Phase 7 Slice
            # 7c). Holds an operator-driven session's bounded, latest-only
            # progress beat (JSON) -- the live-session analogue of a dispatched
            # task's latest_progress, since an operator session has no task.
            cols = [
                r[1]
                for r in conn.execute("PRAGMA table_info(live_sessions)").fetchall()
            ]
            if "latest_progress" not in cols:
                conn.execute(
                    "ALTER TABLE live_sessions ADD COLUMN latest_progress TEXT"
                )
            conn.execute("UPDATE schema_version SET version=?", (12,))
            conn.commit()
            log.info("Schema migrated to version 12: live_sessions.latest_progress")

        if from_version < 13:
            # v12 -> v13: add the `worktree_ownership` reservation table (#2912).
            # A persistent, DB-level per-worktree ACP-ownership reservation the
            # resume verb takes *before* spawning ACP, which a live-session
            # registration must respect -- so ownership and live-registration
            # cannot both win a worktree, even across processes (closing the
            # register-after-check window the #2879 guard only narrows).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worktree_ownership (
                    worktree_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    reserved_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute("UPDATE schema_version SET version=?", (13,))
            conn.commit()
            log.info("Schema migrated to version 13: worktree_ownership table")

        if from_version < 14:
            # v13 -> v14: add the `pending_prompts` durable queue table (#4114).
            # A bridge-owned session that is mid-turn previously rejected a
            # concurrent submit with a 409 and kept no queue -- the only queue
            # lived in the caller (NF's browser tab), so a remount/reload/NF
            # crash silently dropped queued follow-ups. This table makes the
            # queue durable service state: a `queue`-flagged submit against a
            # busy session is persisted here and drained FIFO on turn-settle,
            # surviving a bridge/host restart and usable by host CLI agents too.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    caller_id TEXT,
                    prompt TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_prompts_session "
                "ON pending_prompts(session_id, id)"
            )
            conn.execute("UPDATE schema_version SET version=?", (14,))
            conn.commit()
            log.info("Schema migrated to version 14: pending_prompts table")

        if from_version < 15:
            # v14 -> v15: two-way session succession links for in-place handoff.
            # A handed-off predecessor records ``successor_id``; its successor
            # records ``predecessor_id`` -- so the lineage of work in a worktree
            # is traversable in either direction (single-current-session-per-
            # worktree), not reconstructed from timestamps. ``handoff_at`` stamps
            # when the predecessor was retired in favor of the successor.
            cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            for col, col_type in [
                ("predecessor_id", "TEXT"),
                ("successor_id", "TEXT"),
                ("handoff_at", "REAL"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type}")
                    log.info("Migration v14->v15: added %s column to sessions", col)
            conn.execute("UPDATE schema_version SET version=?", (15,))
            conn.commit()
            log.info("Schema migrated to version 15: session succession links")

        if from_version < 16:
            cols = [
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(live_messages)"
                ).fetchall()
            ]
            if "idempotency_key" not in cols:
                conn.execute(
                    "ALTER TABLE live_messages ADD COLUMN idempotency_key TEXT"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_live_messages_idempotency ON live_messages(idempotency_key)"
                " WHERE idempotency_key IS NOT NULL"
            )
            conn.execute("UPDATE schema_version SET version=?", (16,))
            conn.commit()
            log.info("Schema migrated to version 16: live-message idempotency")

        if from_version < 17:
            conn.execute(
                """
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
                )
                """
            )
            conn.execute("UPDATE schema_version SET version=?", (17,))
            conn.commit()
            log.info(
                "Schema migrated to version 17: delivery cursor invalidations"
            )
        from .db_migrations import migrate_restart_status

        migrate_restart_status(conn, from_version)
