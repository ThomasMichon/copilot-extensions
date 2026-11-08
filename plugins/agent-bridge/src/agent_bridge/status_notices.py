"""Coalesced notice admission through the existing inbox transaction."""

from __future__ import annotations

import hashlib
import time

from .db_core import LIVE_SESSION_STALE_SECONDS
from .live_message_enqueue import LiveMessageEnqueue, _duplicate, _enrolled_admission, _insert
from .runtime_custody import RuntimeCustodyError


def deliver_notice(db, subscription_id: str, *, admission=None, refusal: str | None = None) -> int | None:
    # Serialize the unread epoch, exact inbox admission and notice receipt in
    # one existing write transaction. Crash recovery cannot duplicate a steer,
    # and concurrent acknowledgements cannot admit an obsolete epoch.
    with db._runtime_transaction(write=True) as conn:
        sub = conn.execute(
            "SELECT * FROM bridge_status_subscriptions WHERE id=?", (subscription_id,),
        ).fetchone()
        if sub is None or sub["unread"] <= sub["acked"] or sub["notice_id"] is not None:
            return None
        epoch = conn.execute("SELECT epoch FROM bridge_status_meta").fetchone()[0]
        key = "status-notice:" + hashlib.sha256(
            f"{epoch}:{sub['generation']}:{sub['notice_epoch']}".encode(),
        ).hexdigest()
        pointer = f"/api/v1/status/subscriptions/{subscription_id}/feed"
        request = LiveMessageEnqueue(
            session_id=sub["watcher"], sender="bridge-status",
            body=f"Watched agents have unread status updates. Feed: {pointer}. "
                 "Read is cursor-neutral; explicitly acknowledge the feed to rearm notices.",
            now=time.time(), reply_to=None, kind="notify", delivery="steer",
            expected_session_id=sub["watcher"], idempotency_key=key,
            stale_seconds=LIVE_SESSION_STALE_SECONDS, report_duplicate=False,
            require_idle=False, exact_session=True,
        )
        replay = _duplicate(db, request, None, original_association=True)
        if replay is not None:
            message_id, error = replay
        else:
            try:
                selected = _enrolled_admission(db, conn, request, admission, refusal)
            except RuntimeCustodyError:
                conn.execute(
                    "UPDATE bridge_status_subscriptions SET notice_error='inbox_unavailable' WHERE id=?",
                    (subscription_id,),
                )
                return None
            message_id, error = _insert(db, request, selected, conn.execute)
        conn.execute(
            "UPDATE bridge_status_subscriptions SET notice_id=?,notice_error=? WHERE id=?",
            (message_id, error, subscription_id),
        )
        return message_id
