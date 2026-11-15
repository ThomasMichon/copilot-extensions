"""Native SDK receiving over the existing queue and retained handoff ledger."""

from __future__ import annotations

import json
import uuid

from .db_core import LIVE_SESSION_STALE_SECONDS
from .inbox_custody import InboxConsumer, handoff_stage, observed_time, original_handoff
from .runtime_custody import RuntimeCustodyError, encode

BINDING = "compatible-inbox-v1"


def require_live(conn, session_id: str, now: float) -> None:
    row = conn.execute(
        "SELECT status,updated_at FROM live_sessions WHERE session_id=?", (session_id,),
    ).fetchone()
    if (
        row is None or row["status"] != "live"
        or row["updated_at"] < now - LIVE_SESSION_STALE_SECONDS
        or conn.execute(
            "SELECT 1 FROM live_session_aliases WHERE alias_session_id=?", (session_id,),
        ).fetchone() is not None
    ):
        raise RuntimeCustodyError("Inbox target is not registered live; explicitly resume it first")


def negotiate(db, consumer: InboxConsumer, *, now: float) -> str | None:
    observed_time(now)
    with db._runtime_transaction(write=False) as conn:
        require_live(conn, consumer.session_id, now)
        row = conn.execute(
            "SELECT incarnation FROM bridge_inbox_consumers WHERE session_id=? AND current=1",
            (consumer.session_id,),
        ).fetchone()
        return row["incarnation"] if row is not None else None


def enroll(
    db, consumer: InboxConsumer, *, expected_incarnation: str | None, now: float,
) -> None:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now)
        if conn.execute(
            "SELECT 1 FROM live_messages WHERE session_id=? AND kind LIKE 'control:%' "
            "AND kind != 'control:plugin-readiness' AND delivered_at IS NULL LIMIT 1",
            (consumer.session_id,),
        ).fetchone() is not None:
            raise RuntimeCustodyError("Pending legacy controls prevent retained inbox enrollment")
        document = encode(consumer.document())
        original = conn.execute(
            "SELECT * FROM bridge_inbox_consumers WHERE incarnation=?", (consumer.incarnation,),
        ).fetchone()
        if original is not None:
            if (
                original["session_id"] != consumer.session_id
                or original["installation"] != BINDING or original["current"] != 1
                or original["predecessor"] != expected_incarnation
            ):
                raise RuntimeCustodyError("Inbox receiver identifier is retired or reused")
            return
        current = conn.execute(
            "SELECT incarnation FROM bridge_inbox_consumers WHERE session_id=? AND current=1",
            (consumer.session_id,),
        ).fetchone()
        if (current["incarnation"] if current is not None else None) != expected_incarnation:
            raise RuntimeCustodyError("Inbox receiver changed; reconcile before retry")
        conn.execute(
            "UPDATE bridge_inbox_consumers SET current=0 WHERE session_id=? AND current=1",
            (consumer.session_id,),
        )
        conn.execute(
            "INSERT INTO bridge_inbox_consumers "
            "(incarnation,installation,epoch,session_id,consumer_json,predecessor,current,enrolled_at) "
            "VALUES (?,?,1,?,?,?,1,?)",
            (consumer.incarnation, BINDING, consumer.session_id, document, expected_incarnation, now),
        )


def require_current(conn, consumer: InboxConsumer) -> None:
    row = conn.execute(
        "SELECT 1 FROM bridge_inbox_consumers "
        "WHERE session_id=? AND incarnation=? AND installation=? AND current=1",
        (consumer.session_id, consumer.incarnation, BINDING),
    ).fetchone()
    if row is None:
        raise RuntimeCustodyError("Inbox receiver is not currently enrolled")


def retain_message(conn, message_id: int) -> None:
    row = conn.execute("SELECT * FROM live_messages WHERE id=?", (message_id,)).fetchone()
    if row is None or row["handoff_json"] is not None:
        return
    consumer = conn.execute(
        "SELECT consumer_json FROM bridge_inbox_consumers "
        "WHERE session_id=? AND installation=? AND current=1",
        (row["session_id"], BINDING),
    ).fetchone()
    if consumer is not None:
        conn.execute(
            "UPDATE live_messages SET handoff_json=? WHERE id=?",
            (encode({
                "version": 1, "id": uuid.uuid4().hex, "message_id": message_id,
                "consumer": json.loads(consumer["consumer_json"]),
                "admission": row["runtime_custody_json"],
            }), message_id),
        )


def require_supported(conn, row, now: float) -> None:
    if row["delivery"] not in ("steer", "queue") or row["kind"].casefold().startswith("control:"):
        raise RuntimeCustodyError("Retained inbox control or interrupt delivery remains unsupported")
    if row["kind"].casefold().startswith("startup:"):
        from .live_message_enqueue import LiveMessageEnqueue, _ready_startup

        request = LiveMessageEnqueue(
            session_id=row["session_id"], sender=row["sender"], body=row["body"],
            now=now, reply_to=row["reply_to"], kind=row["kind"], delivery=row["delivery"],
            expected_session_id=row["session_id"], idempotency_key=row["idempotency_key"],
            stale_seconds=LIVE_SESSION_STALE_SECONDS, report_duplicate=False, require_idle=False,
        )
        if not _ready_startup(conn, request):
            raise RuntimeCustodyError("Original startup readiness or input is unsupported")


def poll(db, consumer: InboxConsumer, *, now: float, limit: int | None) -> list[dict]:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now)
        require_current(conn, consumer)
        rows = conn.execute(
            "SELECT * FROM live_messages WHERE session_id=? AND consumed_at IS NULL "
            "AND kind NOT LIKE 'control:%' AND delivered_at IS NULL "
            "AND handoff_started_at IS NULL AND outcome IS NOT 'unknown' "
            "AND (claimed_at IS NULL OR handoff_json IS NOT NULL) "
            "ORDER BY id LIMIT ?", (consumer.session_id, limit or 100),
        ).fetchall()
        result = []
        for row in rows:
            require_supported(conn, row, now)
            if row["handoff_json"] is None:
                retain_message(conn, row["id"])
                row = conn.execute("SELECT * FROM live_messages WHERE id=?", (row["id"],)).fetchone()
            retained = original_handoff(row)
            result.append({
                **dict(row), "handoff": retained, "consumer": retained["consumer"],
                "handoff_stage": handoff_stage(row),
            })
        return result


def begin(db, consumer: InboxConsumer, message_id: int, handoff_id: str, *, now: float) -> bool:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now)
        row = db._original_handoff(
            conn, message_id, consumer, handoff_id, compatible_consumer=True,
        )
        if row["session_id"] != consumer.session_id:
            raise RuntimeCustodyError("Inbox handoff target is unsupported")
        require_supported(conn, row, now)
        if handoff_stage(row) != "offered":
            return False
        conn.execute("UPDATE live_messages SET handoff_started_at=? WHERE id=?", (now, message_id))
        return True
