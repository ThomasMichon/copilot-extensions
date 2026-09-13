"""Database pending-prompt queue helpers."""

from __future__ import annotations

from typing import Any


class _PromptsMixin:
    """Durable pending-prompt queue helpers."""

    def enqueue_prompt(
        self, session_id: str, prompt: str, now: float,
        caller_id: str | None = None,
    ) -> int:
        """Append a prompt to a session's durable pending queue; return its id.

        FIFO order is the autoincrement ``id``. The queue is drained on
        turn-settle (and on resume), so a follow-up submitted while a turn runs
        is delivered exactly once, in order -- surviving a caller remount, an NF
        crash, and a bridge/host restart.
        """
        cur = self.execute_write(
            "INSERT INTO pending_prompts (session_id, caller_id, prompt, "
            "created_at) VALUES (?, ?, ?, ?)",
            (session_id, caller_id, prompt, now),
        )
        return int(cur.lastrowid or 0)

    def list_pending_prompts(self, session_id: str) -> list[dict[str, Any]]:
        """Return a session's queued prompts in FIFO (send) order."""
        rows = self.execute_read(
            "SELECT id, session_id, caller_id, prompt, created_at "
            "FROM pending_prompts WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def transfer_pending_prompts(self, source_id: str, target_id: str) -> int:
        """Move durable follow-ups atomically without changing their FIFO ids."""
        with self._write_lock:
            with self._get_conn() as conn:
                if conn.execute("SELECT 1 FROM sessions WHERE id=?", (target_id,)).fetchone() is None:
                    raise KeyError(f"Session {target_id} not found")
                cursor = conn.execute(
                    "UPDATE pending_prompts SET session_id=? WHERE session_id=?",
                    (target_id, source_id),
                )
                return cursor.rowcount

    def pop_pending_prompt(self, session_id: str) -> dict[str, Any] | None:
        """Atomically remove and return the oldest queued prompt (or None).

        The select-then-delete runs under the write lock so a concurrent drain
        (or a second bridge process sharing the DB) can never pop the same row
        twice.
        """
        with self._write_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT id, session_id, caller_id, prompt, created_at "
                "FROM pending_prompts WHERE session_id=? ORDER BY id ASC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM pending_prompts WHERE id=?", (row["id"],))
            conn.commit()
            return dict(row)

    def remove_pending_prompt(self, session_id: str, queue_id: int) -> bool:
        """Remove one queued prompt by id (operator drops a chip). True if hit."""
        cur = self.execute_write(
            "DELETE FROM pending_prompts WHERE session_id=? AND id=?",
            (session_id, queue_id),
        )
        return cur.rowcount > 0

    def clear_pending_prompts(self, session_id: str) -> int:
        """Drop all queued prompts for a session; return how many were removed.

        Called when the session is stopped / interrupted / ended / deleted --
        mirroring NF's "queue cleared if cancelled, ended, or rolled" so queued
        follow-ups never resurface against a session the operator tore down.
        """
        cur = self.execute_write(
            "DELETE FROM pending_prompts WHERE session_id=?", (session_id,)
        )
        return cur.rowcount

    def count_pending_prompts(self, session_id: str) -> int:
        """Return the number of queued prompts for a session."""
        rows = self.execute_read(
            "SELECT COUNT(*) AS n FROM pending_prompts WHERE session_id=?",
            (session_id,),
        )
        return int(rows[0]["n"]) if rows else 0
