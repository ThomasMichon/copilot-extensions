"""Original-row inbox handoffs under Database's existing write serialization."""

from __future__ import annotations

import sqlite3
import uuid

from .db_core import LIVE_SESSION_STALE_SECONDS
from .db_live_session_aliases import PROCESS_START_TOLERANCE_SECONDS
from .inbox_custody import InboxConsumer, decode_handoff, handoff_stage, observed_time
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, encode, nonblank, positive,
)


class _InboxCustodyMixin:
    @staticmethod
    def _inbox_consumer(consumer: InboxConsumer) -> None:
        if type(consumer) is not InboxConsumer:
            raise RuntimeCustodyError("Inbox custody requires original consumer observations")

    def _inbox_admission(
        self, conn: sqlite3.Connection, identity: str, admission: RuntimeAdmission,
    ) -> None:
        if type(admission) is not RuntimeAdmission or admission.installation != identity:
            raise RuntimeCustodyError("Inbox admission belongs to another installation")
        state = self._runtime_epoch(conn, identity)
        if state.state != "open" or state.epoch != admission.epoch:
            raise RuntimeCustodyError("Inbox effects require the original open epoch")
        if conn.execute(
            "SELECT 1 FROM bridge_runtime_producers WHERE id=? AND installation=? AND epoch=?",
            (admission.producer, identity, admission.epoch),
        ).fetchone() is None:
            raise RuntimeCustodyError("Inbox producer is not enrolled")

    @staticmethod
    def _current_inbox_consumer(
        conn: sqlite3.Connection, consumer: InboxConsumer, now: float,
    ) -> None:
        # Exact native id, not alias resolution: a renamed conversation cannot
        # inherit a send capability even when the OS process did not change.
        row = conn.execute(
            "SELECT * FROM live_sessions WHERE session_id=?", (consumer.session_id,),
        ).fetchone()
        if (
            row is None or row["status"] != "live" or row["pid"] != consumer.pid
            or row["process_started_at"] is None
            or abs(row["process_started_at"] - consumer.process_started_at)
            >= PROCESS_START_TOLERANCE_SECONDS
            or row["updated_at"] < now - LIVE_SESSION_STALE_SECONDS
        ):
            raise RuntimeCustodyError("Inbox consumer registration changed or is unqualified")

    @staticmethod
    def _original_handoff(
        conn: sqlite3.Connection, message_id: int, consumer: InboxConsumer, handoff_id: str,
    ) -> sqlite3.Row:
        nonblank(handoff_id)
        positive(message_id)
        row = conn.execute("SELECT * FROM live_messages WHERE id=?", (message_id,)).fetchone()
        if row is None or row["handoff_json"] is None:
            raise RuntimeCustodyError("Original inbox handoff is absent")
        retained = decode_handoff(row["handoff_json"])
        if (
            retained["message_id"] != message_id or retained["id"] != handoff_id
            or retained["consumer"] != consumer.document()
            or retained["admission"] != row["runtime_custody_json"]
        ):
            raise RuntimeCustodyError("Original inbox handoff observations changed")
        return row

    def poll_live_message_handoffs(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: InboxConsumer, *, now: float,
    ) -> list[dict[str, object]]:
        """Retain each original attempt before returning a body; never grant replay.

        Only owner-associated messages can enter this path. Repeated polls
        return the same attempt and its outcome, not fresh final-send authority.
        Authentication/enrollment of the calling transport remains its owner's
        obligation; these observations alone do not establish native custody.
        """
        self._inbox_consumer(consumer)
        observed_time(now)
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=True) as conn:
            self._inbox_admission(conn, identity, admission)
            self._current_inbox_consumer(conn, consumer, now)
            rows = conn.execute(
                "SELECT * FROM live_messages WHERE session_id=? AND consumed_at IS NULL "
                "AND kind NOT LIKE 'control:%' ORDER BY id",
                (consumer.session_id,),
            ).fetchall()
            result = []
            for row in rows:
                if row["runtime_custody_json"] is None:
                    raise RuntimeCustodyError("Legacy or foreign inbox admission remains unqualified")
                original_admission = RuntimeAdmission.parse(row["runtime_custody_json"])
                self._inbox_admission(conn, identity, original_admission)
                raw = row["handoff_json"]
                if raw is None:
                    if row["delivered_at"] is not None or row["claimed_at"] is not None:
                        raise RuntimeCustodyError("Prior inbox delivery remains unqualified")
                    raw = encode({
                        "version": 1, "id": uuid.uuid4().hex, "message_id": row["id"],
                        "consumer": consumer.document(), "admission": row["runtime_custody_json"],
                    })
                    conn.execute("UPDATE live_messages SET handoff_json=? WHERE id=?",
                                 (raw, row["id"]))
                retained = decode_handoff(raw)
                original = self._original_handoff(conn, row["id"], consumer, retained["id"])
                result.append({**dict(original), "handoff": retained,
                               "handoff_stage": handoff_stage(original)})
            return result

    def begin_live_message_handoff(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: InboxConsumer, message_id: int, handoff_id: str, *, now: float,
    ) -> bool:
        """Reserve one final SDK attempt; a lost reply must never be retried.

        A true return is for this invocation only. Custody remains outstanding
        across the SDK boundary, closure and process death until observed outcome.
        """
        self._inbox_consumer(consumer)
        observed_time(now)
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=True) as conn:
            self._inbox_admission(conn, identity, admission)
            self._current_inbox_consumer(conn, consumer, now)
            row = self._original_handoff(conn, message_id, consumer, handoff_id)
            self._inbox_admission(conn, identity, RuntimeAdmission.parse(row["runtime_custody_json"]))
            if row["session_id"] != consumer.session_id:
                raise RuntimeCustodyError("Inbox handoff cannot move to another native context")
            if handoff_stage(row) != "offered":
                return False
            conn.execute("UPDATE live_messages SET handoff_started_at=? WHERE id=?",
                         (now, message_id))
            return True

    def record_live_message_handoff(
        self, consumer: InboxConsumer, message_id: int, handoff_id: str, *,
        outcome: str, now: float,
    ) -> str:
        """Observe the original SDK outcome after closure/expiry, never resend."""
        self._inbox_consumer(consumer)
        observed_time(now)
        if outcome not in ("sdk_accepted", "consumed", "unknown"):
            raise RuntimeCustodyError("Unsupported inbox handoff outcome")
        with self._runtime_transaction(write=True) as conn:
            row = self._original_handoff(conn, message_id, consumer, handoff_id)
            if row["handoff_started_at"] is None:
                raise RuntimeCustodyError("Inbox handoff never reached final admission")
            if outcome == "sdk_accepted" and row["delivered_at"] is None:
                conn.execute("UPDATE live_messages SET delivered_at=? WHERE id=?", (now, message_id))
            elif outcome == "consumed" and row["consumed_at"] is None:
                conn.execute("UPDATE live_messages SET consumed_at=? WHERE id=?", (now, message_id))
            elif outcome == "unknown" and handoff_stage(row) in ("sending", "unknown"):
                conn.execute("UPDATE live_messages SET outcome='unknown' WHERE id=?", (message_id,))
            current = conn.execute("SELECT * FROM live_messages WHERE id=?", (message_id,)).fetchone()
            return handoff_stage(current)
