"""Database session lifecycle helpers."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
import time
from typing import TYPE_CHECKING, Any, cast
import uuid

from .runtime_custody import RuntimeAdmission, admission_document
from .creation_writer_schema import METADATA_RECOVERY_HELD, RETAINED_RECOVERY_HELD

if TYPE_CHECKING:
    from .db import Database
    from .session_host.host_index import HostRecord


class _SessionsMixin:
    """Bridge-owned session lifecycle helpers."""

    def retained_recovery_held(self, session_id: str) -> bool:
        self = cast("Database", self)
        return bool(self.execute_read(
            "SELECT 1 FROM sessions WHERE id=? AND "
            f"({self._retained_recovery_held_predicate()})", (session_id,),
        ))

    def _retained_recovery_held_predicate(self, conn: sqlite3.Connection | None = None) -> str:
        self = cast("Database", self)
        conn = self._get_conn() if conn is None else conn
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acquisition_launch_gates'"
        ).fetchone() is None:
            return f"({METADATA_RECOVERY_HELD}) OR ({RETAINED_RECOVERY_HELD})"
        return (
            "sessions.id IN (SELECT session_id FROM acquisition_launch_gates "
            "WHERE driver='retained-recovery' AND session_id IS NOT NULL) OR "
            f"({METADATA_RECOVERY_HELD}) OR ({RETAINED_RECOVERY_HELD})"
        )

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
        caller_session_id: str | None = None,
        acp_session_id: str | None = None,
        background_recovery_enabled: bool = True,
        runtime_admission: RuntimeAdmission | None = None,
        _on_create: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        self = cast("Database", self)
        statement = (
            "INSERT INTO sessions (id, name, agent_name, caller_id, "
            "caller_session_id, target_dir, "
            "target_type, status, config_json, target_json, "
            "background_recovery_enabled, created_at, updated_at, acp_session_id, "
            "runtime_custody_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        parameters = (session_id, name, agent_name, caller_id, caller_session_id,
             target_dir, target_type,
             status, config_json, target_json,
             int(background_recovery_enabled and runtime_admission is None),
             now, now, acp_session_id, admission_document(runtime_admission))
        if _on_create is not None:
            if runtime_admission is not None:
                raise ValueError("Ordinary native import cannot borrow runtime admission")
            with self._runtime_transaction(write=True, full_sync=True, membership=True) as conn:
                conn.execute(statement, parameters)
                _on_create(conn)
            return
        self._execute_runtime_admitted_write(
            statement, parameters, runtime_admission, membership=True,
        )

    def update_session_status(
        self, session_id: str, status: str, now: float, pid: int | None = None
    ) -> None:
        self = cast("Database", self)
        if pid is not None:
            self.execute_write(
                "UPDATE sessions SET status=?, pid=?, updated_at=? WHERE id=?",
                (status, pid, now, session_id),
                membership=True,
            )
        else:
            self.execute_write(
                "UPDATE sessions SET status=?, pid=NULL, updated_at=? WHERE id=?",
                (status, now, session_id),
                membership=True,
            )

    def update_session_stopped(
        self, session_id: str, now: float, background_recovery_enabled: bool,
    ) -> bool:
        """Atomically persist STOPPED status + the dormancy gate together.

        Two separate writes (status, then the gate) could leave a row
        durably 'stopped' but still recovery-enabled if the daemon is
        interrupted between them -- an interrupted stop must stay
        conservatively dormant (review of #3058).
        """
        self = cast("Database", self)
        if self.read_only:
            raise PermissionError("Read-only database cannot execute writes")
        conn = self._get_conn()
        with self._write_lock:
            try:
                conn.execute(
                    "UPDATE sessions SET status='stopped', pid=NULL, "
                    "background_recovery_enabled=CASE WHEN ?=1 "
                    "AND cold_recovery_stop_token IS NULL "
                    "AND runtime_custody_json IS NULL THEN 1 ELSE 0 END, "
                    "updated_at=? WHERE id=?",
                    (1 if background_recovery_enabled else 0, now, session_id),
                )
                row = conn.execute(
                    "SELECT background_recovery_enabled FROM sessions WHERE id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(session_id)
                conn.commit()
                return bool(row["background_recovery_enabled"])
            except BaseException:
                conn.rollback()
                raise

    def update_session_acp_id(self, session_id: str, acp_session_id: str) -> None:
        """Persist the ACP session ID for resume support."""
        self = cast("Database", self)
        self.execute_write(
            "UPDATE sessions SET acp_session_id=? WHERE id=?",
            (acp_session_id, session_id),
            membership=True,
        )

    def has_stopped_host_receipt(self, observed: dict[str, Any], record: HostRecord) -> bool:
        """Read-only prefilter; retirement must recheck the receipt under both locks."""
        from .host_exit_history import matches_stopped_receipt, stopped_event

        self = cast("Database", self)
        if (observed["status"] != "stopped" or observed["id"] != record.session_id
                or not observed.get("acp_session_id") or record.boundary != "local"):
            return False
        receipt = stopped_event(record, observed["acp_session_id"], observed["created_at"])
        return matches_stopped_receipt(self._get_conn(), record.session_id, receipt, observed["updated_at"])

    def settle_exited_inherited_host(self, observed: dict[str, Any], record: HostRecord) -> bool:
        """Close ordinary interrupted work, under the exact exited Host's retirement lock."""
        from .host_exit_history import matches_stopped_receipt, reconcile_idle, stopped_event, terminal_outcome
        from .status_store import StatusStore

        self = cast("Database", self)
        sid = observed["id"]
        if sid != record.session_id or not observed.get("acp_session_id") or record.boundary != "local":
            return False
        receipt = stopped_event(record, observed["acp_session_id"], observed["created_at"])
        self.flush()
        now = time.time()
        with self._runtime_transaction(write=True, membership=True) as conn:
            held = self._retained_recovery_held_predicate(conn)
            current = conn.execute(
                "SELECT status,pid,background_recovery_enabled,updated_at FROM sessions "
                "WHERE id=? AND acp_session_id=? AND created_at=? AND target_type='local' "
                "AND (pid IS NULL OR pid=?) AND cold_recovery_stop_token IS NULL "
                "AND runtime_custody_json IS NULL AND NOT EXISTS (SELECT 1 FROM cold_outages WHERE session_id=?) "
                "AND NOT EXISTS (SELECT 1 FROM turns WHERE session_id=? AND completed_at IS NULL AND "
                "(runtime_custody_json IS NOT NULL OR native_handoff_json IS NOT NULL OR "
                "native_admission_json IS NOT NULL OR acquisition_delivery_id IS NOT NULL)) "
                f"AND NOT ({held})",
                (sid, observed["acp_session_id"], observed["created_at"], record.child_pid, sid, sid),
            ).fetchone()
            if current is None:
                return False
            turns = conn.execute(
                "SELECT turn_index,started_at FROM turns WHERE session_id=? AND completed_at IS NULL LIMIT 2", (sid,),
            ).fetchall()
            if current["status"] == "stopped" and current["pid"] is None and not current["background_recovery_enabled"]:
                return not turns and matches_stopped_receipt(conn, sid, receipt, current["updated_at"])
            if (current["status"] not in ("running", "idle") or current["status"] != observed["status"]
                or not current["background_recovery_enabled"] or current["updated_at"] != observed["updated_at"]
                or len(turns) > 1):
                return False
            events = [("session_state_changed", receipt)]
            turn = conn.execute(
                "SELECT turn_index,started_at,completed_at,stop_reason,"
                "(runtime_custody_json IS NULL AND native_handoff_json IS NULL AND "
                "native_admission_json IS NULL AND acquisition_delivery_id IS NULL) AS ordinary "
                "FROM turns WHERE session_id=? ORDER BY turn_index DESC LIMIT 1", (sid,),
            ).fetchone()
            if turn is not None:
                if (not turn["ordinary"] or turn["started_at"] is None
                    or (turns and turns[0]["turn_index"] != turn["turn_index"])):
                    return False
                try:
                    outcome = terminal_outcome(conn, sid, turn)
                except ValueError:
                    return False
                if turn["completed_at"] is not None and (outcome is None or turn["stop_reason"] != outcome.reason):
                    return False
                if outcome is not None:
                    if not reconcile_idle(self, conn, sid, outcome):
                        return False
                if turn["completed_at"] is None:
                    if outcome is None:
                        events.insert(0, ("turn_complete", '{"stop_reason":"interrupted"}'))
                        reason, finished = "interrupted", now
                    else:
                        reason, finished = outcome.reason, outcome.timestamp
                    conn.execute("UPDATE turns SET stop_reason=?,completed_at=? WHERE session_id=? AND turn_index=?",
                                 (reason, finished, sid, turn["turn_index"]))
            conn.execute("UPDATE sessions SET status='stopped',pid=NULL,background_recovery_enabled=0,updated_at=? "
                         "WHERE id=?", (now, sid))
            for kind, body in events:
                inserted = conn.execute(
                    "INSERT INTO events (session_id,event_id,event_type,data_json,timestamp) "
                    "SELECT ?,COALESCE(MAX(event_id),0)+1,?,?,? FROM events WHERE session_id=? RETURNING event_id",
                    (sid, kind, body, now, sid),
                ).fetchone()
                if kind == "turn_complete":
                    StatusStore(self).publish(
                        sid, "idle", f"host-exit:{sid}:{observed['acp_session_id']}:{inserted[0]}",
                        transaction=conn, observed_at=now, evidence="historical_transition",
                    )
        return True

    def update_session_background_recovery(
        self, session_id: str, enabled: bool, *, expected_stop_token: str | None = None,
    ) -> bool:
        """Persist recovery permission only if no newer durable stop forbids it."""
        self = cast("Database", self)
        cursor = self.execute_write(
            "UPDATE sessions SET background_recovery_enabled=? WHERE id=? "
            "AND (?=0 OR (cold_recovery_stop_token IS ? AND runtime_custody_json IS NULL))",
            (1 if enabled else 0, session_id, 1 if enabled else 0, expected_stop_token),
        )
        return enabled and cursor.rowcount == 1

    def request_cold_recovery_stop(self, session_id: str) -> str:
        """Persist fresh stop intent before teardown can yield to recovery."""
        self = cast("Database", self)
        token = uuid.uuid4().hex
        cursor = self.execute_write(
            "UPDATE sessions SET cold_recovery_stop_token=?, "
            "background_recovery_enabled=0 WHERE id=?",
            (token, session_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(session_id)
        return token

    def clear_cold_recovery_stop(self, session_id: str, expected_token: str | None) -> bool:
        """Clear only the stop intent observed by an admitted manual resume."""
        self = cast("Database", self)
        cursor = self.execute_write(
            "UPDATE sessions SET cold_recovery_stop_token=NULL "
            "WHERE id=? AND cold_recovery_stop_token IS ?",
            (session_id, expected_token),
        )
        return cursor.rowcount == 1

    def update_session_target(
        self, session_id: str, target_json: str, target_dir: str | None = None,
    ) -> None:
        """Persist updated target (e.g. after spawn resolves worktree_id/cwd)."""
        self = cast("Database", self)
        self.execute_write(
            "UPDATE sessions SET target_json=?, target_dir=? WHERE id=?",
            (target_json, target_dir, session_id),
            membership=True,
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
        self = cast("Database", self)
        self.execute_write(
            "UPDATE sessions SET context_size=?, context_used=?, "
            "usage_model=?, last_usage_at=?, updated_at=? WHERE id=?",
            (context_size, context_used, usage_model, now, now, session_id),
        )

    def link_succession(
        self, predecessor_id: str, successor_id: str, now: float
    ) -> None:
        """Record a two-way handoff link between a predecessor and its successor.

        Stamps ``successor_id``/``handoff_at`` on the retiring predecessor and
        ``predecessor_id`` on the fresh successor, so the worktree's session
        lineage is traversable in either direction after the changeover (and
        survives a daemon restart).
        """
        self = cast("Database", self)
        self.execute_write(
            "UPDATE sessions SET successor_id=?, handoff_at=?, updated_at=? "
            "WHERE id=?",
            (successor_id, now, now, predecessor_id),
        )
        self.execute_write(
            "UPDATE sessions SET predecessor_id=?, updated_at=? WHERE id=?",
            (predecessor_id, now, successor_id),
        )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        self = cast("Database", self)
        rows = self.execute_read("SELECT * FROM sessions WHERE id=?", (session_id,))
        if rows:
            return dict(rows[0])
        return None

    def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        self = cast("Database", self)
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

    def delete_session(self, session_id: str) -> None:
        self = cast("Database", self)
        self.flush()
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                recovery_held = self._retained_recovery_held_predicate(conn)
                if conn.execute(
                    "SELECT 1 FROM sessions WHERE id=? AND "
                    f"(runtime_custody_json IS NOT NULL OR ({recovery_held})) "
                    "UNION ALL SELECT 1 FROM turns WHERE session_id=? "
                    "AND runtime_custody_json IS NOT NULL",
                    (session_id, session_id),
                ).fetchone() is not None:
                    from .runtime_custody import RuntimeCustodyError

                    raise RuntimeCustodyError("Retained runtime custody requires outcome reconciliation")
                # Child rows must precede the FK-protected parent deletion.
                conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
                conn.execute(
                    "DELETE FROM delivery_cursors WHERE session_id=?", (session_id,)
                )
                conn.execute(
                    "DELETE FROM delivery_cursor_invalidations WHERE session_id=?",
                    (session_id,),
                )
                from .managed_messages import removed

                removed(conn, session_id)
                conn.execute("DELETE FROM pending_prompts WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
