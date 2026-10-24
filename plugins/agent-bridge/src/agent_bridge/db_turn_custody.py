"""One-shot handoffs on original turns; no transport enrollment or SDK calls."""

from __future__ import annotations

import sqlite3

from .inbox_custody import observed_time
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, nonblank,
)
from .turn_custody import NativeTurnConsumer, handoff_stage, original_handoff, turn_index


class _TurnCustodyMixin:
    @staticmethod
    def _turn_consumer(consumer: NativeTurnConsumer, index: int) -> None:
        if type(consumer) is not NativeTurnConsumer:
            raise RuntimeCustodyError("Native turn custody requires original consumer observations")
        turn_index(index)

    @staticmethod
    def _native_turn(conn: sqlite3.Connection, consumer: NativeTurnConsumer, index: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM turns WHERE session_id=? AND turn_index=?",
            (consumer.session_id, index),
        ).fetchone()
        if row is None:
            raise RuntimeCustodyError("Original native turn is absent")
        return row

    @staticmethod
    def _native_context(conn: sqlite3.Connection, consumer: NativeTurnConsumer) -> None:
        row = conn.execute(
            "SELECT acp_session_id,status FROM sessions WHERE id=?", (consumer.session_id,),
        ).fetchone()
        if (
            row is None or row["acp_session_id"] != consumer.native_session_id
            or row["status"] not in ("idle", "running")
        ):
            raise RuntimeCustodyError("Current native turn context changed or is unqualified")

    def _original_native_handoff(
        self, conn: sqlite3.Connection, consumer: NativeTurnConsumer, index: int, handoff_id: str,
    ) -> sqlite3.Row:
        nonblank(handoff_id)
        row = self._native_turn(conn, consumer, index)
        if row["native_handoff_json"] is None:
            raise RuntimeCustodyError("Original native turn handoff is absent")
        handoff = original_handoff(row)
        if handoff["id"] != handoff_id or handoff["consumer"] != consumer.document():
            raise RuntimeCustodyError("Original native turn observations changed")
        return row

    def prepare_native_turn_handoff(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: NativeTurnConsumer, index: int, *, expected_prompt: str,
    ) -> dict[str, object]:
        """Observe the handoff atomically retained by original turn admission.

        The actual transport must supply/recheck its live native reference and
        incarnation. These internal observations do not enroll that transport.
        """
        self._turn_consumer(consumer, index)
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=False) as conn:
            self._runtime_admission(conn, identity, admission, cohort="Native turn")
            self._native_context(conn, consumer)
            row = self._native_turn(conn, consumer, index)
            if type(expected_prompt) is not str or row["prompt"] != expected_prompt:
                raise RuntimeCustodyError("Original native turn input changed")
            if row["runtime_custody_json"] is None:
                raise RuntimeCustodyError("Legacy native turn admission remains unqualified")
            self._runtime_admission(
                conn, identity, RuntimeAdmission.parse(row["runtime_custody_json"]), cohort="Native turn",
            )
            if row["native_handoff_json"] is None:
                if row["completed_at"] is not None:
                    raise RuntimeCustodyError("Prior native turn outcome remains unqualified")
                raise RuntimeCustodyError("Original native turn handoff is absent")
            handoff = original_handoff(row)
            return original_handoff(self._original_native_handoff(
                conn, consumer, index, handoff["id"],
            ))

    def begin_native_turn_handoff(
        self, installation: RuntimeInstallation, admission: RuntimeAdmission,
        consumer: NativeTurnConsumer, index: int, handoff_id: str, *,
        expected_prompt: str, now: float,
    ) -> bool:
        """Authorize this invocation only; a lost true reply stays outstanding."""
        self._turn_consumer(consumer, index)
        observed_time(now)
        identity = self._runtime_installation(installation)
        with self._runtime_transaction(write=True) as conn:
            self._runtime_admission(conn, identity, admission, cohort="Native turn")
            self._native_context(conn, consumer)
            row = self._original_native_handoff(conn, consumer, index, handoff_id)
            if type(expected_prompt) is not str or row["prompt"] != expected_prompt:
                raise RuntimeCustodyError("Original native turn input changed")
            self._runtime_admission(
                conn, identity, RuntimeAdmission.parse(row["runtime_custody_json"]), cohort="Native turn",
            )
            if handoff_stage(row) != "offered":
                return False
            if row["completed_at"] is not None:
                raise RuntimeCustodyError("Prior native turn outcome remains unqualified")
            conn.execute(
                "UPDATE turns SET native_handoff_started_at=? WHERE session_id=? AND turn_index=?",
                (now, consumer.session_id, index),
            )
            return True

    def record_native_turn_handoff(
        self, consumer: NativeTurnConsumer, index: int, handoff_id: str, *,
        outcome: str, now: float,
    ) -> str:
        """Record the original outcome even after closure or context replacement."""
        self._turn_consumer(consumer, index)
        observed_time(now)
        if outcome not in ("native_accepted", "consumed", "unknown"):
            raise RuntimeCustodyError("Unsupported native turn handoff outcome")
        with self._runtime_transaction(write=True) as conn:
            row = self._original_native_handoff(conn, consumer, index, handoff_id)
            if row["native_handoff_started_at"] is None:
                raise RuntimeCustodyError("Native turn handoff never reached final admission")
            if outcome in ("native_accepted", "consumed"):
                column = "native_accepted_at" if outcome == "native_accepted" else "native_consumed_at"
                if row[column] is None:
                    conn.execute(
                        f"UPDATE turns SET {column}=? WHERE session_id=? AND turn_index=?",
                        (now, consumer.session_id, index),
                    )
            elif handoff_stage(row) in ("sending", "unknown") and row["native_outcome"] is None:
                conn.execute(
                    "UPDATE turns SET native_outcome='unknown' WHERE session_id=? AND turn_index=?",
                    (consumer.session_id, index),
                )
            return handoff_stage(self._native_turn(conn, consumer, index))

    def read_native_turn_handoff(
        self, consumer: NativeTurnConsumer, index: int, handoff_id: str,
    ) -> dict[str, object]:
        """Read metadata from prepared storage without grant, repair or enrollment."""
        self._turn_consumer(consumer, index)
        with self._runtime_transaction(write=False) as conn:
            row = self._original_native_handoff(conn, consumer, index, handoff_id)
            return {
                "handoff": original_handoff(row), "stage": handoff_stage(row),
                **{name: row[name] for name in (
                    "native_handoff_started_at", "native_accepted_at", "native_consumed_at",
                )},
            }
