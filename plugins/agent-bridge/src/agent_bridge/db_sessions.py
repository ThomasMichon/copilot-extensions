"""Database session lifecycle helpers."""

from __future__ import annotations

from typing import Any


class _SessionsMixin:
    """Bridge-owned session lifecycle helpers."""

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
            "target_type, status, config_json, target_json, "
            "background_recovery_enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, name, agent_name, caller_id, target_dir, target_type,
             status, config_json, target_json, 1, now, now),
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

    def update_session_background_recovery(
        self, session_id: str, enabled: bool
    ) -> None:
        """Persist whether daemon background recovery may touch a session."""
        self.execute_write(
            "UPDATE sessions SET background_recovery_enabled=? WHERE id=?",
            (1 if enabled else 0, session_id),
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

    def link_succession(
        self, predecessor_id: str, successor_id: str, now: float
    ) -> None:
        """Record a two-way handoff link between a predecessor and its successor.

        Stamps ``successor_id``/``handoff_at`` on the retiring predecessor and
        ``predecessor_id`` on the fresh successor, so the worktree's session
        lineage is traversable in either direction after the changeover (and
        survives a daemon restart).
        """
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
            conn.execute(
                "DELETE FROM delivery_cursor_invalidations WHERE session_id=?",
                (session_id,),
            )
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.commit()
