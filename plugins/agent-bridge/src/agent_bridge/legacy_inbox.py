"""Legacy body exposure retains its delivery owner across receiver enrollment."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .db_live_session_aliases import MESSAGE_TARGET_SQL
from .durable_inbox import LEGACY_SUPPORTED_SQL
from .runtime_custody import RuntimeCustodyError

if TYPE_CHECKING:
    from .db import Database

_PENDING = (
    "SELECT * FROM live_messages "
    f"WHERE session_id={MESSAGE_TARGET_SQL} AND delivered_at IS NULL AND kind NOT LIKE 'control:%' "
    "AND disposition_json IS NULL "
    "AND ((exact_session=0 AND producer_target_json IS NULL) OR NOT EXISTS ("
    "SELECT 1 FROM live_session_aliases WHERE alias_session_id=live_messages.session_id)) "
    "AND consumed_at IS NULL AND handoff_json IS NULL "
    "AND (producer_target_json IS NULL OR claimed_at IS NOT NULL OR NOT EXISTS ("
    "SELECT 1 FROM bridge_inbox_consumers WHERE session_id=live_messages.session_id)) "
    f"AND {LEGACY_SUPPORTED_SQL} "
    "AND (? OR exact_session=0) AND (? OR kind NOT LIKE 'startup:prompt:%') "
    "AND runtime_custody_json IS NULL ORDER BY id ASC"
)


def pending(
    db: Database, session_id: str, *, exact_native: bool, plugin_readiness: bool,
) -> list[dict[str, Any]]:
    params = (session_id, session_id, session_id, exact_native, plugin_readiness)
    rows = db.execute_read(_PENDING, params)
    if not any(row["producer_target_json"] is not None and row["claimed_at"] is None for row in rows):
        return [dict(row) for row in rows]
    with db._runtime_transaction(write=True) as conn:
        rows = conn.execute(_PENDING, params).fetchall()
        now = time.time()
        result = []
        for row in rows:
            item = dict(row)
            if row["producer_target_json"] is not None and row["claimed_at"] is None:
                claimed = conn.execute(
                    "UPDATE live_messages SET claimed_at=? WHERE id=? AND claimed_at IS NULL "
                    "AND producer_target_json IS NOT NULL", (now, row["id"]),
                )
                if claimed.rowcount != 1:
                    raise RuntimeCustodyError("Legacy delivery ownership changed before body exposure")
                item["claimed_at"] = now
            result.append(item)
        return result


def owns(db: Database, session_id: str, message_ids: list[int]) -> bool:
    if not message_ids:
        return False
    placeholders = ",".join("?" for _ in message_ids)
    return bool(db.execute_read(
        "SELECT 1 FROM live_messages WHERE session_id=? AND producer_target_json IS NOT NULL "
        "AND claimed_at IS NOT NULL AND handoff_json IS NULL "
        f"AND id IN ({placeholders}) LIMIT 1", (session_id, *message_ids),
    ))
