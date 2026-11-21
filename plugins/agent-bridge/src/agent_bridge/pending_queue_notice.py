"""Bounded diagnostic projection of emitted hook notices, never inbox authority."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

TTL = 60.0
MAX_OBSERVATIONS = 1024


@dataclass(frozen=True)
class _Notice:
    count: int
    incomplete: bool
    delivery_path: str
    consumer_incarnation: str | None
    observed_at: float
    monotonic_at: float


class QueueNoticeObservations:
    """Keep only count/coverage metadata already emitted by the existing hook."""

    def __init__(self) -> None:
        self._rows: OrderedDict[str, _Notice] = OrderedDict()

    def record(self, session_id: str, data: dict[str, Any]) -> None:
        count = data.get("pending_count")
        incomplete = data.get("incomplete")
        path = data.get("delivery_path")
        incarnation = data.get("consumer_incarnation")
        if (
            type(count) is not int
            or not 0 <= count <= (1 << 53) - 1
            or type(incomplete) is not bool
            or not isinstance(path, str)
            or path not in {"legacy", "retained"}
            or (
                incarnation is not None
                and (not isinstance(incarnation, str) or not 1 <= len(incarnation) <= 128)
            )
        ):
            return
        self._rows[session_id] = _Notice(
            count,
            incomplete,
            path,
            incarnation,
            time.time(),
            time.monotonic(),
        )
        self._rows.move_to_end(session_id)
        while len(self._rows) > MAX_OBSERVATIONS:
            self._rows.popitem(last=False)

    def inspect(self, session_id: str, incarnation: str | None) -> dict[str, Any]:
        row = self._rows.get(session_id)
        unknown = {
            "availability": "unavailable",
            "scope": "last-emitted-hook-notice-only",
            "reason": "No recent hook notice observed by this serving process",
        }
        if row is None or time.monotonic() - row.monotonic_at > TTL:
            return unknown
        if row.consumer_incarnation is not None and row.consumer_incarnation != incarnation:
            return {**unknown, "reason": "Hook notice belongs to a different receiver incarnation"}
        return {
            "availability": "available",
            "scope": "last-emitted-hook-notice-only",
            "pending_count": row.count,
            "incomplete": row.incomplete,
            "delivery_path": row.delivery_path,
            "observed_at": row.observed_at,
            "age_seconds": max(0.0, time.monotonic() - row.monotonic_at),
            "receiver_binding": "matched"
            if row.consumer_incarnation is not None
            else "unverified",
            "reason": "Diagnostic notice, not a current SDK queue census or receipt",
        }


observations = QueueNoticeObservations()
