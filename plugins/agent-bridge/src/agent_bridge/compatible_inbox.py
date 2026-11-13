"""Compatible receiving over the existing queue and one-shot handoff records."""

from __future__ import annotations

import uuid

from .db_core import LIVE_SESSION_STALE_SECONDS
from .inbox_custody import InboxConsumer, handoff_stage, observed_time, original_handoff
from .runtime_custody import RuntimeCustodyError, encode

BINDING = "compatible-inbox-v1"


def require_live(conn, session_id: str, now: float) -> None:
    row = conn.execute(
        "SELECT status,updated_at FROM live_sessions WHERE session_id=?", (session_id,),
    ).fetchone()
    if row is None or row["status"] != "live" or row["updated_at"] < now - LIVE_SESSION_STALE_SECONDS:
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


def enroll(db, consumer: InboxConsumer, *, now: float) -> None:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now)
        if conn.execute(
            "SELECT 1 FROM live_messages WHERE session_id=? AND kind LIKE 'control:%' "
            "AND kind != 'control:plugin-readiness' AND delivered_at IS NULL LIMIT 1",
            (consumer.session_id,),
        ).fetchone() is not None:
            raise RuntimeCustodyError("Pending legacy controls prevent retained inbox enrollment")
        # This is receiver presence, not installation or process custody. Old
        # enrollment rows remain readable for original handoff recovery.
        original = conn.execute(
            "SELECT session_id FROM bridge_inbox_consumers WHERE incarnation=?",
            (consumer.incarnation,),
        ).fetchone()
        if original is not None:
            if original["session_id"] != consumer.session_id:
                raise RuntimeCustodyError("Inbox receiver identifier belongs to another target")
            return
        conn.execute(
            "UPDATE bridge_inbox_consumers SET current=0 WHERE session_id=? AND current=1",
            (consumer.session_id,),
        )
        conn.execute(
            "INSERT INTO bridge_inbox_consumers "
            "(incarnation,installation,epoch,session_id,consumer_json,predecessor,current,enrolled_at) "
            "VALUES (?,?,1,?,?,NULL,1,?)",
            (consumer.incarnation, BINDING, consumer.session_id, encode(consumer.document()), now),
        )


def retain_message(conn, message_id: int) -> None:
    row = conn.execute("SELECT * FROM live_messages WHERE id=?", (message_id,)).fetchone()
    if row is None or row["handoff_json"] is not None:
        return
    consumer = conn.execute(
        "SELECT consumer_json FROM bridge_inbox_consumers WHERE session_id=? AND current=1 LIMIT 1",
        (row["session_id"],),
    ).fetchone()
    if consumer is not None:
        import json

        retained = {
            "version": 1, "id": uuid.uuid4().hex, "message_id": message_id,
            "consumer": json.loads(consumer["consumer_json"]), "admission": row["runtime_custody_json"],
        }
        conn.execute(
            "UPDATE live_messages SET handoff_json=? WHERE id=?", (encode(retained), message_id),
        )


def poll(db, consumer: InboxConsumer, *, now: float, limit: int | None) -> list[dict]:
    observed_time(now)
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
        raise RuntimeCustodyError("Inbox poll requires a bounded integer limit")
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now)
        rows = conn.execute(
            "SELECT * FROM live_messages WHERE session_id=? AND consumed_at IS NULL "
            "AND kind NOT LIKE 'control:%' AND (delivered_at IS NULL OR handoff_json IS NOT NULL) "
            "ORDER BY id LIMIT ?", (consumer.session_id, limit or 100),
        ).fetchall()
        result = []
        for row in rows:
            if row["delivery"] not in ("steer", "queue"):
                raise RuntimeCustodyError("Retained inbox interrupt delivery remains unsupported")
            if row["handoff_json"] is None:
                if row["claimed_at"] is not None:
                    raise RuntimeCustodyError("Prior inbox delivery outcome is unknown; do not resend")
                retained = {
                    "version": 1, "id": uuid.uuid4().hex, "message_id": row["id"],
                    "consumer": consumer.document(), "admission": row["runtime_custody_json"],
                }
                conn.execute(
                    "UPDATE live_messages SET handoff_json=? WHERE id=?", (encode(retained), row["id"]),
                )
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
        row = db._original_handoff(conn, message_id, consumer, handoff_id)
        if row["session_id"] != consumer.session_id or row["delivery"] not in ("steer", "queue"):
            raise RuntimeCustodyError("Inbox handoff target or delivery is unsupported")
        if handoff_stage(row) != "offered":
            return False
        conn.execute("UPDATE live_messages SET handoff_started_at=? WHERE id=?", (now, message_id))
        return True
