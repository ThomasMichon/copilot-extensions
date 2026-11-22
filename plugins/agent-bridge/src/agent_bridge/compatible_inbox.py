"""Native SDK receiving over the existing queue and retained handoff ledger."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING, Any, cast
import uuid

from .db_core import LIVE_SESSION_STALE_SECONDS
from .inbox_custody import InboxConsumer, handoff_stage, observed_time, original_handoff
from .live_message_schema import PREDICATE, require_schema
from . import retained_attempts
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, encode

if TYPE_CHECKING:
    from .db import Database

BINDING = "compatible-inbox-v1"


def receiver_identity(document: dict[str, Any]) -> dict[str, Any]:
    """Keep advisory process diagnostics separate from native receiver identity."""
    return {key: value for key, value in document.items() if key not in ("pid", "process_started_at")}


def require_live(
    conn: sqlite3.Connection, session_id: str, now: float, *, native_only: bool = False,
) -> None:
    row = conn.execute(
        "SELECT * FROM live_sessions WHERE session_id=?", (session_id,),
    ).fetchone()
    if (
        row is None or row["status"] != "live"
        or row["updated_at"] < now - LIVE_SESSION_STALE_SECONDS
        or conn.execute(
            "SELECT 1 FROM live_session_aliases WHERE alias_session_id=?", (session_id,),
        ).fetchone() is not None
    ):
        raise RuntimeCustodyError("Inbox target is not registered live; explicitly resume it first")
    if native_only and row["venue"] is not None:
        raise RuntimeCustodyError("Automatic SDK compatibility requires a registered native target")
    if row["worktree_id"]:
        owner = conn.execute(
            "SELECT session_id FROM live_sessions WHERE worktree_id=? AND status='live' "
            "AND updated_at>=? AND (? IS NULL OR ? IS NULL OR "
            "(machine=? COLLATE NOCASE AND repo=? COLLATE NOCASE)) "
            "ORDER BY registered_at DESC,updated_at DESC LIMIT 1",
            (row["worktree_id"], now - LIVE_SESSION_STALE_SECONDS,
             row["machine"], row["repo"], row["machine"], row["repo"]),
        ).fetchone()
        if owner is None or owner["session_id"] != session_id:
            raise RuntimeCustodyError("Inbox target is not the current worktree session")


def negotiate(
    db: Database, consumer: InboxConsumer, *, now: float, native_only: bool = False,
) -> str | None:
    observed_time(now)
    with db._runtime_transaction(write=False) as conn:
        require_live(conn, consumer.session_id, now, native_only=native_only)
        row = conn.execute(
            "SELECT incarnation FROM bridge_inbox_consumers WHERE session_id=? AND current=1",
            (consumer.session_id,),
        ).fetchone()
        return cast(str, row["incarnation"]) if row is not None else None


def enroll(
    db: Database, consumer: InboxConsumer, *, expected_incarnation: str | None, now: float,
    native_only: bool = False,
) -> None:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now, native_only=native_only)
        document = encode(consumer.document())
        original = conn.execute(
            "SELECT * FROM bridge_inbox_consumers WHERE incarnation=?", (consumer.incarnation,),
        ).fetchone()
        if original is not None:
            if (
                original["session_id"] != consumer.session_id
                or original["installation"] != BINDING or original["current"] != 1
                or original["predecessor"] != expected_incarnation
                or receiver_identity(json.loads(original["consumer_json"])) != receiver_identity(consumer.document())
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


def require_current(conn: sqlite3.Connection, consumer: InboxConsumer) -> None:
    row = conn.execute(
        "SELECT consumer_json FROM bridge_inbox_consumers "
        "WHERE session_id=? AND incarnation=? AND installation=? AND current=1",
        (consumer.session_id, consumer.incarnation, BINDING),
    ).fetchone()
    if row is None or receiver_identity(json.loads(row["consumer_json"])) != receiver_identity(consumer.document()):
        raise RuntimeCustodyError("Inbox receiver is not currently enrolled")


def require_control_admission(
    db: Database, conn: sqlite3.Connection, row: sqlite3.Row | None,
) -> None:
    if conn.execute("SELECT 1 FROM bridge_runtime_epoch WHERE state='closed'").fetchone() is not None:
        raise RuntimeCustodyError("Native control admission is closed")
    if row is not None and row["runtime_custody_json"] is not None:
        original = RuntimeAdmission.parse(row["runtime_custody_json"])
        db._inbox_admission(conn, original.installation, original)


def retain_message(conn: sqlite3.Connection, message_id: int) -> None:
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


def reconcile_offers(
    db: Database, session_id: str, worktree_id: str, incarnation: str, *,
    after: int, limit: int,
) -> list[int]:
    """Attach missing original offers without claiming, submitting or retrying input."""
    if (
        type(after) is not int or not 0 <= after <= (1 << 63) - 1
        or type(limit) is not int or not 1 <= limit <= 100
    ):
        raise RuntimeCustodyError("Offer reconciliation requires bounded page arguments")
    now = time.time()
    with db._runtime_transaction(write=True) as conn:
        require_schema(conn)
        require_live(conn, session_id, now)
        target = conn.execute(
            "SELECT worktree_id FROM live_sessions WHERE session_id=?", (session_id,),
        ).fetchone()
        if target["worktree_id"] != worktree_id:
            raise RuntimeCustodyError("Pending queue worktree binding changed")
        current = conn.execute(
            "SELECT consumer_json FROM bridge_inbox_consumers WHERE session_id=? "
            "AND incarnation=? AND installation=? AND current=1",
            (session_id, incarnation, BINDING),
        ).fetchone()
        if current is None:
            raise RuntimeCustodyError("Current compatible inbox receiver is unavailable or changed")
        consumer = InboxConsumer(**json.loads(current["consumer_json"]))
        require_current(conn, consumer)
        # Page the original unresolved order, not a filtered list that jumps over
        # uncertain work. The native consumer keeps final admission and send custody.
        rows = conn.execute(
            "SELECT id,delivery,kind,handoff_json,runtime_custody_json,claimed_at,"
            "handoff_started_at,delivered_at,outcome FROM live_messages "
            f"WHERE session_id=? AND id>? AND {PREDICATE} "
            "ORDER BY id LIMIT ?", (session_id, after, limit),
        ).fetchall()
        prepared = []
        for row in rows:
            if (
                row["handoff_json"] is not None or row["runtime_custody_json"] is not None
                or row["claimed_at"] is not None or row["handoff_started_at"] is not None
                or row["delivered_at"] is not None or row["outcome"] is not None
                or row["delivery"] not in ("steer", "queue", "interrupt")
                or row["kind"].casefold().startswith("startup:")
            ):
                continue
            retain_message(conn, row["id"])
            prepared.append(row["id"])
        return prepared


def require_supported(
    conn: sqlite3.Connection, row: sqlite3.Row, now: float, *, versioned: bool = False,
) -> None:
    deliveries = ("steer", "queue", "interrupt") if versioned else ("steer", "queue")
    if row["delivery"] not in deliveries or row["kind"].casefold().startswith("control:"):
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


def poll(
    db: Database, consumer: InboxConsumer, *, now: float, limit: int | None,
    native_only: bool = False, reattempt: list[int] | None = None,
) -> list[dict[str, Any]]:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now, native_only=native_only)
        require_current(conn, consumer)
        from .message_contracts import envelope, pending_controls

        controls = pending_controls(conn, consumer, limit or 100, now=now)
        # An idle current receiver may re-attempt a started original that was
        # never marked submitted, in original order and through begin's bound.
        if consumer.message_base != 1 or not retained_attempts.available(conn):
            reattempt = None
        rows = conn.execute(
            "SELECT * FROM live_messages WHERE session_id=? AND consumed_at IS NULL "
            "AND kind NOT LIKE 'control:%' AND delivered_at IS NULL "
            "AND disposition_json IS NULL AND (("
            "handoff_started_at IS NULL AND outcome IS NOT 'unknown' "
            "AND (claimed_at IS NULL OR handoff_json IS NOT NULL)) "
            "OR (handoff_started_at IS NOT NULL AND handoff_json IS NOT NULL "
            "AND delivery!='interrupt' "
            "AND id IN (SELECT value FROM json_each(?)))) "
            "ORDER BY id LIMIT ?",
            (consumer.session_id, encode(sorted(set(reattempt or []))), limit or 100),
        ).fetchall()
        result = []
        for row in rows:
            if row["handoff_started_at"] is not None:
                retained = original_handoff(row)
                result.append({
                    **dict(row), "handoff": retained, "consumer": retained["consumer"],
                    "handoff_stage": handoff_stage(row),
                    "attempt": retained_attempts.count(conn, row["id"]),
                    "message_contract": envelope(conn, row, consumer),
                })
                continue
            if consumer.message_base is None:
                from .message_contracts import dispose, require_supported as require_contract

                try:
                    require_contract(envelope(conn, row, consumer), row["kind"])
                    require_supported(conn, row, now)
                except RuntimeCustodyError:
                    dispose(
                        conn, consumer, row["id"], "rejected", "unsupported_reader",
                        "This reader cannot enforce the stored message contract.", now=now,
                    )
                    continue
            if row["handoff_json"] is None:
                retain_message(conn, row["id"])
                row = conn.execute("SELECT * FROM live_messages WHERE id=?", (row["id"],)).fetchone()
            retained = original_handoff(row)
            if consumer.message_base is None and receiver_identity(retained["consumer"]) != receiver_identity(consumer.document()):
                from .message_contracts import dispose

                dispose(
                    conn, consumer, row["id"], "rejected", "unsupported_reader",
                    "This reader cannot adopt an unstarted offer from an earlier receiver.", now=now,
                )
                continue
            result.append({
                **dict(row), "handoff": retained, "consumer": retained["consumer"],
                "handoff_stage": handoff_stage(row),
                **({"message_contract": envelope(conn, row, consumer)} if consumer.message_base == 1 else {}),
            })
        return sorted([*controls, *result], key=lambda row: row["id"])[:limit or 100]


def begin(
    db: Database, consumer: InboxConsumer, message_id: int, handoff_id: str, *, now: float,
    native_only: bool = False, attempt: int | None = None,
) -> bool:
    observed_time(now)
    with db._runtime_transaction(write=True) as conn:
        require_live(conn, consumer.session_id, now, native_only=native_only)
        require_current(conn, consumer)
        original = conn.execute(
            "SELECT * FROM live_messages WHERE id=? AND session_id=?",
            (message_id, consumer.session_id),
        ).fetchone()
        if original is None:
            raise RuntimeCustodyError("Original inbox handoff is absent")
        original_consumer = InboxConsumer(**original_handoff(original)["consumer"])
        row = db._original_handoff(
            conn, message_id, original_consumer, handoff_id, compatible_consumer=True,
        )
        if row["session_id"] != consumer.session_id:
            raise RuntimeCustodyError("Inbox handoff target is unsupported")
        require_supported(conn, row, now, versioned=consumer.message_base == 1)
        from .message_contracts import envelope, require_supported as require_contract

        header = envelope(conn, row, consumer)
        require_contract(header, row["kind"])
        if row["disposition_json"] is not None:
            raise RuntimeCustodyError("Original message already has a disposition")
        stage = handoff_stage(row)
        if (
            stage in ("sending", "unknown") and attempt is not None
            and consumer.message_base == 1 and row["delivery"] != "interrupt"
        ):
            return retained_attempts.regrant(
                conn, row, consumer.document(), header, attempt, now=now,
            )
        if stage != "offered":
            return False
        if row["delivery"] == "interrupt":
            require_control_admission(db, conn, row)
        if row["delivery"] == "interrupt" and conn.execute(
            "SELECT 1 FROM live_messages WHERE session_id=? AND id!=? "
            "AND handoff_started_at IS NOT NULL AND consumed_at IS NULL "
            "AND disposition_json IS NULL LIMIT 1",
            (consumer.session_id, message_id),
        ).fetchone() is not None:
            raise RuntimeCustodyError("Interrupt waits for original native consumption; no abort or replay")
        conn.execute("UPDATE live_messages SET handoff_started_at=? WHERE id=?", (now, message_id))
        return True
