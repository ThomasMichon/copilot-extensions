"""Body-free observations of the original backend inbox, not a second queue."""

from __future__ import annotations

import time
from typing import Any

from . import compatible_inbox, pending_queue_notice
from .db import Database
from .inbox_custody import handoff_stage
from .live_delivery_state import LiveDeliveryState
from .runtime_custody import RuntimeCustodyError

SCHEMA = "agent-bridge.pending-queue"
VERSION = 1
CONTRACT = {
    "schema": SCHEMA,
    "version": VERSION,
    "inspection": "backend-inbox-metadata",
    "reconciliation": "prepare-original-offers",
}
MAX_ID = (1 << 63) - 1


def inspect(
    db: Database,
    session_id: str,
    worktree_id: str,
    *,
    limit: int = 50,
    after: int = 0,
    attention: LiveDeliveryState | None = None,
) -> dict[str, Any]:
    """Observe one exact target/page without polling, claiming or settling input."""
    if (
        type(after) is not int
        or not 0 <= after <= MAX_ID
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise RuntimeCustodyError("Queue inspection requires bounded page arguments")
    now = time.time()
    with db._runtime_transaction(write=False) as conn:
        target = conn.execute(
            "SELECT session_id,worktree_id,status,updated_at,turn_state,pid,process_started_at "
            "FROM live_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if target is None:
            raise LookupError("Exact native registration is unavailable")
        if target["worktree_id"] != worktree_id:
            raise RuntimeCustodyError("Pending queue worktree binding changed")
        current = conn.execute(
            "SELECT incarnation,installation FROM bridge_inbox_consumers "
            "WHERE session_id=? AND current=1",
            (session_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT id,session_id,created_at,delivered_at,consumed_at,handoff_started_at,"
            "outcome,handoff_json IS NOT NULL AS retained,consumption_tracked,"
            "claimed_at,kind,delivery FROM live_messages "
            "WHERE session_id=? AND id>? AND consumed_at IS NULL "
            "AND disposition_json IS NULL AND kind NOT LIKE 'control:%' "
            "ORDER BY id LIMIT ?",
            (session_id, after, limit + 1),
        ).fetchall()
        try:
            compatible_inbox.require_live(conn, session_id, now)
            freshness = "current"
        except RuntimeCustodyError:
            freshness = "stale-or-retired"
    messages = []
    for row in rows[:limit]:
        stage = (
            handoff_stage(row)
            if row["retained"]
            else (
                "sdk_accepted"
                if row["delivered_at"] is not None
                else "unknown"
                if row["outcome"] == "unknown" or row["claimed_at"] is not None
                else "admitted"
            )
        )
        messages.append(
            {
                "message_id": row["id"],
                "stage": stage,
                "kind": row["kind"][:128],
                "delivery": row["delivery"],
                "admitted_at": row["created_at"],
                "sdk_accepted_at": row["delivered_at"],
                "handoff_started_at": row["handoff_started_at"],
                "consumption_tracked": bool(row["consumption_tracked"]),
            }
        )
    notice = pending_queue_notice.observations.inspect(
        session_id,
        current["incarnation"] if current else None,
    )
    return {
        **CONTRACT,
        "observed_at": now,
        "target": {
            "session_id": session_id,
            "worktree_id": worktree_id,
            "registration": target["status"],
            "registration_updated_at": target["updated_at"],
            "freshness": freshness,
            "turn_state": target["turn_state"],
            "consumer_incarnation": current["incarnation"] if current else None,
        },
        "coverage": {
            "backend": "exact-native-session",
            "receiver_pending_notice": notice["availability"],
            "native_sdk_queue": "unavailable",
            "controls": "excluded",
            "consumption": "only-recorded-original-receipts",
        },
        "receiver_notice": notice,
        "attention": attention.inspect(dict(target))
        if attention is not None
        else {
            "availability": "unavailable",
            "state": "unknown",
            "reasons": [],
            "reason": "native delivery observation unavailable",
        },
        "messages": messages,
        "page": {
            "after": after,
            "limit": limit,
            "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
            "complete": len(rows) <= limit,
        },
        "reconcile_supported": bool(
            freshness == "current"
            and current
            and current["installation"] == compatible_inbox.BINDING
        ),
    }
