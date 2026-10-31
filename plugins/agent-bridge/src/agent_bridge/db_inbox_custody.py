"""Original-row inbox handoffs under Database's existing write serialization."""

from __future__ import annotations

import sqlite3
import uuid

from .db_core import LIVE_SESSION_STALE_SECONDS
from .db_live_session_aliases import PROCESS_START_TOLERANCE_SECONDS
from .inbox_custody import InboxConsumer, decode_handoff, handoff_stage, observed_time, original_handoff
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
        self._runtime_admission(conn, identity, admission, cohort="Inbox")

    @staticmethod
    def _registered_inbox_consumer(
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
            or row["venue"] is not None
            or conn.execute(
                "SELECT 1 FROM live_session_aliases WHERE alias_session_id=?",
                (consumer.session_id,),
            ).fetchone() is not None
        ):
            raise RuntimeCustodyError("Inbox consumer registration changed or is unqualified")

    def enroll_inbox_consumer(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: InboxConsumer, *, expected_incarnation: str | None, now: float,
    ) -> None:
        """CAS current local observations; retired incarnations never reenroll.

        The extension owns minting a new incarnation on reload or SDK-context
        change. An exact retry recovers a lost enrollment reply, not an old send.
        This internal method does not authenticate or enroll a transport.
        """
        self._inbox_consumer(consumer)
        observed_time(now)
        if expected_incarnation is not None:
            nonblank(expected_incarnation)
        identity = self._runtime_installation(installation)
        document = encode(consumer.document())
        with self._runtime_transaction(write=True) as conn:
            self._inbox_admission(conn, identity, admission)
            self._registered_inbox_consumer(conn, consumer, now)
            if conn.execute(
                "SELECT 1 FROM live_messages WHERE session_id=? AND kind LIKE 'control:%' "
                "AND delivered_at IS NULL LIMIT 1", (consumer.session_id,),
            ).fetchone() is not None:
                raise RuntimeCustodyError("Pending legacy controls prevent retained inbox enrollment")
            original = conn.execute(
                "SELECT * FROM bridge_inbox_consumers WHERE incarnation=?",
                (consumer.incarnation,),
            ).fetchone()
            if original is not None:
                if (
                    original["current"] != 1 or original["installation"] != identity
                    or original["epoch"] != admission.epoch
                    or original["session_id"] != consumer.session_id
                    or original["consumer_json"] != document
                    or original["predecessor"] != expected_incarnation
                ):
                    raise RuntimeCustodyError("Inbox consumer incarnation is retired or reused")
                return
            if conn.execute(
                "SELECT 1 FROM live_messages WHERE handoff_json IS NOT NULL "
                "AND json_extract(handoff_json,'$.consumer.incarnation')=? LIMIT 1",
                (consumer.incarnation,),
            ).fetchone() is not None:
                raise RuntimeCustodyError("Historical inbox incarnation cannot gain new enrollment")
            current = conn.execute(
                "SELECT incarnation FROM bridge_inbox_consumers WHERE session_id=? AND current=1",
                (consumer.session_id,),
            ).fetchone()
            if (current["incarnation"] if current is not None else None) != expected_incarnation:
                raise RuntimeCustodyError("Inbox consumer enrollment changed; reconcile before retry")
            conn.execute(
                "UPDATE bridge_inbox_consumers SET current=0 WHERE session_id=? AND current=1",
                (consumer.session_id,),
            )
            conn.execute(
                "INSERT INTO bridge_inbox_consumers "
                "(incarnation,installation,epoch,session_id,consumer_json,predecessor,current,enrolled_at) "
                "VALUES (?,?,?,?,?,?,1,?)",
                (consumer.incarnation, identity, admission.epoch, consumer.session_id,
                 document, expected_incarnation, now),
            )

    def observe_inbox_consumer(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: InboxConsumer, *, now: float,
    ) -> str | None:
        """Observe the current predecessor without enrolling or retiring it."""
        self._inbox_consumer(consumer)
        observed_time(now)
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=False) as conn:
            self._inbox_admission(conn, identity, admission)
            self._registered_inbox_consumer(conn, consumer, now)
            row = conn.execute(
                 "SELECT incarnation FROM bridge_inbox_consumers WHERE session_id=? AND current=1",
                 (consumer.session_id,),
            ).fetchone()
            return row["incarnation"] if row is not None else None

    def _current_inbox_consumer(
        self, conn: sqlite3.Connection, consumer: InboxConsumer, now: float,
        identity: str, epoch: int,
    ) -> None:
        self._registered_inbox_consumer(conn, consumer, now)
        current = conn.execute(
            "SELECT * FROM bridge_inbox_consumers WHERE incarnation=? AND current=1",
            (consumer.incarnation,),
        ).fetchone()
        if (
            current is None or current["installation"] != identity or current["epoch"] != epoch
            or current["session_id"] != consumer.session_id
            or current["consumer_json"] != encode(consumer.document())
        ):
            raise RuntimeCustodyError("Inbox consumer is not the current enrolled incarnation")

    @staticmethod
    def _original_handoff(
        conn: sqlite3.Connection, message_id: int, consumer: InboxConsumer, handoff_id: str,
    ) -> sqlite3.Row:
        nonblank(handoff_id)
        positive(message_id)
        row = conn.execute("SELECT * FROM live_messages WHERE id=?", (message_id,)).fetchone()
        if row is None or row["handoff_json"] is None:
            raise RuntimeCustodyError("Original inbox handoff is absent")
        retained = original_handoff(row)
        if (
            retained["id"] != handoff_id
            or retained["consumer"] != consumer.document()
        ):
            raise RuntimeCustodyError("Original inbox handoff observations changed")
        return row

    def poll_live_message_handoffs(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: InboxConsumer, *, now: float, limit: int | None = None,
    ) -> list[dict[str, object]]:
        """Retain each original attempt before returning a body; never grant replay.

        Only owner-associated messages can enter this path. Repeated polls
        return the same attempt and its outcome, not fresh final-send authority.
        Authentication/enrollment of the calling transport remains its owner's
        obligation; these observations alone do not establish native custody.
        """
        self._inbox_consumer(consumer)
        observed_time(now)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
            raise RuntimeCustodyError("Inbox poll requires a bounded integer limit")
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=True) as conn:
            self._inbox_admission(conn, identity, admission)
            self._current_inbox_consumer(conn, consumer, now, identity, admission.epoch)
            rows = conn.execute(
                "SELECT * FROM live_messages WHERE session_id=? AND consumed_at IS NULL "
                "AND kind NOT LIKE 'control:%' ORDER BY id" + (" LIMIT ?" if limit is not None else ""),
                (consumer.session_id, limit) if limit is not None else (consumer.session_id,),
            ).fetchall()
            result = []
            for row in rows:
                if row["runtime_custody_json"] is None:
                    raise RuntimeCustodyError("Legacy or foreign inbox admission remains unqualified")
                if row["delivery"] not in ("steer", "queue"):
                    raise RuntimeCustodyError("Retained inbox interrupt delivery remains unsupported")
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
            self._current_inbox_consumer(conn, consumer, now, identity, admission.epoch)
            row = self._original_handoff(conn, message_id, consumer, handoff_id)
            self._inbox_admission(conn, identity, RuntimeAdmission.parse(row["runtime_custody_json"]))
            if row["session_id"] != consumer.session_id:
                raise RuntimeCustodyError("Inbox handoff cannot move to another native context")
            if row["delivery"] not in ("steer", "queue"):
                raise RuntimeCustodyError("Retained inbox interrupt delivery remains unsupported")
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
