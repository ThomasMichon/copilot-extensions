"""``TaskQueue`` mixin: explicit reconciliation of historical legacy reservations.

The startup-fencing migration gives pre-existing rows ``startup_complete=0``
and ``startup_fenced=0`` without inferring launch completion. Pool placement
treats any such unbound row as accountable legacy capacity, so a long-lived
queue cannot adopt claim-before-spawn until that history is explicitly
reconciled. This operator-attested operation fences terminal, unbound history
created before a cutoff. It never touches active reservations, pending native
release, pending conclusions, retained transport or a native still shared with
live work, and it preserves every row.

Composed into :class:`agent_dispatch.queue.TaskQueue`; relies on
``self._connect()``, ``self._audit()`` and ``self._now()``.
"""

from __future__ import annotations

import collections
import math
from typing import Any

from .queue_pool_accountability import ReservationAccountability
from .queue_records import SpawnState, Status, TaskError

_SAMPLE = 20


class SpawnHistoryMixin:
    """``fence_legacy_history`` for :class:`TaskQueue`."""

    def fence_legacy_history(
        self, *, before: float, apply: bool = False, permitted: bool = False,
        confirmed_no_live_holder: bool = False, reason: str = "",
    ) -> dict[str, Any]:
        """Preview, or apply, startup fencing and native retirement for old history."""
        if type(before) not in (int, float) or not math.isfinite(before) or before <= 0:
            raise TaskError("history reconciliation requires a finite positive cutoff")
        if apply and (not permitted or not confirmed_no_live_holder or not reason.strip()):
            raise TaskError(
                "history reconciliation requires permission, verified no-live-holder attestation and reason",
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            accounting = ReservationAccountability(conn)
            receipts = accounting.receipts
            active = tuple(SpawnState.ACTIVE)
            marks = ",".join("?" * len(active))
            live_handles: set[str] = set()
            for row in conn.execute(
                f"SELECT key, session_handle FROM spawn_reservations WHERE state IN ({marks})"
                " OR native_release_pending=1", active,
            ):
                if row["session_handle"]:
                    live_handles.add(row["session_handle"])
                if receipts.get(row["key"]):
                    live_handles.add(receipts[row["key"]])
            owners = {
                row["owner_session_id"] for row in conn.execute(
                    "SELECT owner_session_id FROM tasks WHERE owner_session_id IS NOT NULL",
                )
            }
            concluded = tuple(Status.CONCLUDED)
            latest_live = {
                row["key"] for row in conn.execute(
                    "SELECT s.key FROM spawn_reservations s JOIN tasks t ON t.id = s.task_id"
                    f" WHERE t.status NOT IN ({','.join('?' * len(concluded))})"
                    " AND s.session_handle IS NOT NULL AND s.reserved_at = ("
                    "  SELECT MAX(o.reserved_at) FROM spawn_reservations o"
                    "  WHERE o.task_id = s.task_id AND o.session_handle IS NOT NULL)",
                    concluded,
                )
            }
            rows = conn.execute(
                "SELECT s.* FROM spawn_reservations s"
                " LEFT JOIN pool_reservation_bindings b ON b.reservation_key = s.key"
                " WHERE b.reservation_key IS NULL ORDER BY s.reserved_at",
            ).fetchall()
            excluded: collections.Counter[str] = collections.Counter()
            eligible: list[Any] = []
            for row in rows:
                reason_key = self._history_exclusion(
                    row, before, accounting, receipts, live_handles | owners, latest_live,
                )
                if reason_key is None:
                    eligible.append(row)
                elif reason_key != "not_accountable":
                    excluded[reason_key] += 1
            result: dict[str, Any] = {
                "before": before, "applied": False, "eligible": len(eligible),
                "excluded": dict(sorted(excluded.items())),
                "sample": [row["key"] for row in eligible[:_SAMPLE]],
            }
            if not apply:
                conn.rollback()
                return result
            timestamp = self._now(None)
            by_task: dict[str, list[str]] = collections.defaultdict(list)
            for row in eligible:
                conn.execute(
                    "UPDATE spawn_reservations SET startup_fenced=1,"
                    " conversation_retired=CASE WHEN session_handle IS NOT NULL"
                    " THEN 1 ELSE conversation_retired END, updated_at=? WHERE key=?",
                    (timestamp, row["key"]),
                )
                by_task[row["task_id"]].append(row["key"])
            for task_id, keys in by_task.items():
                status_row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
                status = status_row["status"] if status_row else None
                self._audit(
                    conn, task_id, ts=timestamp, from_status=status, to_status=status,
                    worker="operator",
                    note=(
                        f"legacy history reconciled: {len(keys)} reservation(s) fenced before "
                        f"{before}; no-live-holder attested; history preserved; reason={reason.strip()}"
                    ),
                )
            conn.commit()
        result["applied"] = True
        result["fenced"] = len(eligible)
        result["tasks"] = len(by_task)
        return result

    @staticmethod
    def _history_exclusion(
        row: Any, before: float, accounting: ReservationAccountability,
        receipts: dict[str, Any], live: set[str], latest_live: set[str],
    ) -> str | None:
        if row["state"] in SpawnState.ACTIVE:
            return "active"
        if row["native_release_pending"]:
            return "native_release_pending"
        try:
            if not accounting.accountable(row):
                return "not_accountable"
        except (TaskError, ValueError):
            return "unclassifiable"
        if row["reserved_at"] is None or row["reserved_at"] >= before:
            return "after_cutoff"
        if row["conclusion_state"] == "pending":
            return "conclusion_pending"
        history = {**dict(row), "native_session_id": receipts.get(row["key"])}
        if accounting.transport.accountable(history):
            return "transport"
        natives = {row["session_handle"], receipts.get(row["key"])} - {None}
        if natives & live:
            return "shared_live_native"
        if row["key"] in latest_live:
            return "resumable_task_native"
        return None
