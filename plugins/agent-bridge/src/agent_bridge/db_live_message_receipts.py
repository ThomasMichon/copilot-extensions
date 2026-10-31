"""Read-only receipt/inbox projections and attributed consumption evidence."""

from __future__ import annotations

import sqlite3
from typing import Any

from .db_live_session_aliases import CANONICAL_SESSION_SQL as _CANON, MESSAGE_TARGET_SQL
from .inbox_custody import handoff_stage, original_handoff
from .runtime_custody import RuntimeCustodyError, nonblank, positive

_HANDOFF_COLUMNS = (
    "id,session_id,created_at,delivered_at,consumed_at,handoff_json,"
    "runtime_custody_json,handoff_started_at,outcome"
)
MAX_RECOVERY_MESSAGE_ID = (1 << 63) - 1


def _handoff_receipt(row: sqlite3.Row) -> dict[str, Any]:
    retained = original_handoff(row)
    return {
        "session_id": row["session_id"], "message_id": row["id"],
        "handoff_id": retained["id"], "consumer": retained["consumer"],
        "stage": handoff_stage(row), "admitted_at": row["created_at"],
        "handoff_started_at": row["handoff_started_at"],
        "sdk_accepted_at": row["delivered_at"], "consumed_at": row["consumed_at"],
    }


class _LiveMessageReceiptsMixin:
    def live_message_handoff_receipt(
        self, session_id: str, message_id: int,
    ) -> dict[str, Any] | None:
        """Read original handoff facts after retirement without fetching its body."""
        nonblank(session_id)
        positive(message_id)
        if message_id > MAX_RECOVERY_MESSAGE_ID:
            raise RuntimeCustodyError("Inbox recovery message id exceeds SQLite's integer range")
        rows = self.execute_read(
            f"SELECT {_HANDOFF_COLUMNS} FROM live_messages WHERE id=? AND session_id=? "
            "AND handoff_json IS NOT NULL AND kind NOT LIKE 'control:%'",
            (message_id, session_id),
        )
        return _handoff_receipt(rows[0]) if rows else None

    def inspect_live_handoff_recovery(
        self, session_id: str, *, limit: int, after: int = 0,
    ) -> dict[str, Any]:
        """Page unresolved original handoffs; no aliases, body, cursor or writes."""
        nonblank(session_id)
        if (
            type(limit) is not int or not 1 <= limit <= 100
            or type(after) is not int or not 0 <= after <= MAX_RECOVERY_MESSAGE_ID
        ):
            raise RuntimeCustodyError("Inbox recovery requires bounded integer page arguments")
        rows = self.execute_read(
            f"SELECT {_HANDOFF_COLUMNS} FROM live_messages WHERE session_id=? AND id>? "
            "AND handoff_json IS NOT NULL AND consumed_at IS NULL "
            "AND kind NOT LIKE 'control:%' ORDER BY id LIMIT ?",
            (session_id, after, limit + 1),
        )
        return {
            "messages": [_handoff_receipt(row) for row in rows[:limit]],
            "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

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
