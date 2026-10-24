"""Read-only receipt/inbox projections and attributed consumption evidence."""

from __future__ import annotations

from typing import Any

from .db_live_session_aliases import CANONICAL_SESSION_SQL as _CANON, MESSAGE_TARGET_SQL


class _LiveMessageReceiptsMixin:
    def live_message_receipt(self, session_id: str, message_id: int) -> dict[str, Any] | None:
        """Return the retained admission and delivery evidence for one message."""
        rows = self.execute_read(
            "SELECT session_id, id AS message_id, created_at AS admitted_at, "
            "delivered_at AS sdk_accepted_at, consumed_at FROM live_messages "
            f"WHERE session_id={MESSAGE_TARGET_SQL} AND id=? AND kind NOT LIKE 'control:%'",
            (session_id, session_id, session_id, message_id),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["stage"] = (
            "consumed" if row["consumed_at"] is not None else
            "sdk_accepted" if row["sdk_accepted_at"] is not None else "admitted"
        )
        return row

    def consume_live_message(self, session_id: str, message_id: int, now: float) -> int:
        """Record consumption once, independently of the SDK-send acknowledgement."""
        cur = self.execute_write(
            "UPDATE live_messages SET consumed_at=? "
            f"WHERE session_id={_CANON} AND id=? AND consumed_at IS NULL "
            "AND handoff_json IS NULL AND runtime_custody_json IS NULL "
            "AND kind NOT LIKE 'control:%'",
            (now, session_id, session_id, message_id),
        )
        return cur.rowcount

    def mark_live_message_acceptance_busy(self, session_id: str, ids: list[int], now: float) -> int:
        """Fence ACK-derived busy state against already-observed consumption."""
        if not ids:
            return 0
        # Match this ACK's timestamp, not older accepted IDs in a mixed retry.
        placeholders = ",".join("?" for _ in ids)
        cur = self.execute_write(
            "UPDATE live_sessions SET turn_state='running', last_activity_at=? "
            f"WHERE session_id={_CANON} AND EXISTS ("
            "SELECT 1 FROM live_messages WHERE session_id=live_sessions.session_id "
            f"AND id IN ({placeholders}) AND delivered_at=? AND consumed_at IS NULL "
            "AND kind NOT LIKE 'control:%')",
            (now, session_id, session_id, *ids, now),
        )
        return cur.rowcount

    def inspect_live_inbox(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        """Bounded cursor-neutral metadata, including accepted but unconsumed input."""
        rows = self.execute_read(
            "SELECT id AS message_id, sender, kind, delivery, created_at AS admitted_at, "
            "delivered_at AS sdk_accepted_at FROM live_messages "
            f"WHERE session_id IN (?, {_CANON}) AND session_id={MESSAGE_TARGET_SQL} "
            "AND consumed_at IS NULL "
            "AND (delivered_at IS NULL OR consumption_tracked=1) "
            "AND kind NOT LIKE 'control:%' ORDER BY id ASC LIMIT ?",
            (session_id, session_id, session_id, session_id, session_id, session_id, limit),
        )
        return [dict(row) for row in rows]
