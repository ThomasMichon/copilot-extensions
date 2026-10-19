"""Atomic claiming, withdrawal and inspection of extension controls."""

from __future__ import annotations

from typing import Any

from .db_live_session_aliases import CANONICAL_SESSION_SQL as _CANON


class LiveControlsMixin:
    def claim_live_controls(
        self, session_id: str, now: float, max_age: float
    ) -> list[dict[str, Any]]:
        """Claim a session's pending controls for its extension to apply.

        One transaction selects the unclaimed controls and stamps
        ``claimed_at``, so each control goes to exactly one poll, and a
        claimed control can no longer be withdrawn by its requester's timeout
        (see :meth:`withdraw_live_control`). Controls older than ``max_age``
        are expired instead: their requester is gone (e.g. the daemon
        restarted mid-wait), so they must never apply late.
        """
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                session_id = conn.execute(
                    f"SELECT {_CANON}", (session_id, session_id)
                ).fetchone()[0]
                conn.execute(
                    "UPDATE live_messages SET delivered_at=?, outcome='expired' "
                    "WHERE session_id=? AND kind LIKE 'control:%' "
                    "AND delivered_at IS NULL AND claimed_at IS NULL AND created_at < ?",
                    (now, session_id, now - max_age),
                )
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM live_messages WHERE session_id=? "
                    "AND kind LIKE 'control:%' AND delivered_at IS NULL "
                    "AND claimed_at IS NULL ORDER BY id ASC",
                    (session_id,),
                ).fetchall()]
                if rows:
                    placeholders = ",".join("?" for _ in rows)
                    conn.execute(
                        f"UPDATE live_messages SET claimed_at=? WHERE id IN ({placeholders})",
                        (now, *(r["id"] for r in rows)),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return rows

    def withdraw_live_control(self, session_id: str, control_id: int, now: float) -> bool:
        """Withdraw a control nobody has claimed yet; True when withdrawn.

        False means the extension already claimed it (it may be applying it
        now) or it already has an outcome -- the requester must then report
        that outcome, or that the change is in flight, never "not applied".
        """
        cur = self.execute_write(
            "UPDATE live_messages SET delivered_at=?, outcome='withdrawn' "
            f"WHERE session_id={_CANON} AND id=? AND kind LIKE 'control:%' "
            "AND delivered_at IS NULL AND claimed_at IS NULL",
            (now, session_id, session_id, control_id),
        )
        return cur.rowcount == 1

    def live_control_state(
        self, session_id: str, control_id: int, *, include_body: bool = False,
    ) -> dict[str, Any] | None:
        """A control's ``claimed_at`` and ``outcome`` (``None`` if unknown)."""
        columns = "claimed_at, outcome, kind, body" if include_body else "claimed_at, outcome"
        rows = self.execute_read(
            f"SELECT {columns} FROM live_messages "
            f"WHERE session_id={_CANON} AND id=? AND kind LIKE 'control:%'",
            (session_id, session_id, control_id),
        )
        return dict(rows[0]) if rows else None
