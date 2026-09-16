"""Database event, turn, and cursor helpers."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any


class _EventsMixin:
    """Event-log, turn, and delivery-cursor helpers."""

    def delete_events(self, session_id: str) -> None:
        """Delete all persisted events for a session (keeps the session row).

        Used by the resync flow, which rebuilds the event log from the
        agent's authoritative load-time replay.
        """
        self.flush()
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            conn.commit()

    def begin_event_rebuild(
        self,
        session_id: str,
        *,
        prior_head_id: int,
        prior_continuity_id: str | None,
        timestamp: float,
    ) -> None:
        """Atomically invalidate cursors and remove the prior event generation."""
        self.flush()
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO delivery_cursor_invalidations "
                    "(caller_id, session_id, prior_last_acked_id, prior_head_id, "
                    "prior_continuity_id, current_continuity_id, invalidated_at) "
                    "SELECT caller_id, session_id, last_acked_id, ?, ?, NULL, ? "
                    "FROM delivery_cursors WHERE session_id=? "
                    "ON CONFLICT(caller_id, session_id) DO UPDATE SET "
                    "prior_last_acked_id=excluded.prior_last_acked_id, "
                    "prior_head_id=excluded.prior_head_id, "
                    "prior_continuity_id=excluded.prior_continuity_id, "
                    "current_continuity_id=NULL, "
                    "invalidated_at=excluded.invalidated_at",
                    (
                        prior_head_id,
                        prior_continuity_id,
                        timestamp,
                        session_id,
                    ),
                )
                conn.execute(
                    "UPDATE delivery_cursor_invalidations SET "
                    "current_continuity_id=NULL, invalidated_at=? "
                    "WHERE session_id=?",
                    (timestamp, session_id),
                )
                conn.execute(
                    "DELETE FROM delivery_cursors WHERE session_id=?",
                    (session_id,),
                )
                conn.execute(
                    "DELETE FROM events WHERE session_id=?", (session_id,)
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def create_turn(
        self, session_id: str, turn_index: int, prompt: str, now: float
    ) -> None:
        self.execute_write(
            "INSERT INTO turns (session_id, turn_index, prompt, started_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, turn_index, prompt, now),
        )

    def update_turn(
        self,
        session_id: str,
        turn_index: int,
        *,
        response_text: str | None = None,
        thought_text: str | None = None,
        stop_reason: str | None = None,
        tool_calls_json: str | None = None,
        completed_at: float | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        if response_text is not None:
            updates.append("response_text=?")
            params.append(response_text)
        if thought_text is not None:
            updates.append("thought_text=?")
            params.append(thought_text)
        if stop_reason is not None:
            updates.append("stop_reason=?")
            params.append(stop_reason)
        if tool_calls_json is not None:
            updates.append("tool_calls_json=?")
            params.append(tool_calls_json)
        if completed_at is not None:
            updates.append("completed_at=?")
            params.append(completed_at)
        if not updates:
            return
        params.extend([session_id, turn_index])
        self.execute_write(
            f"UPDATE turns SET {', '.join(updates)} "
            f"WHERE session_id=? AND turn_index=?",
            tuple(params),
        )

    def get_turns(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.execute_read(
            "SELECT * FROM turns WHERE session_id=? ORDER BY turn_index",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def get_turn(self, session_id: str, turn_index: int) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM turns WHERE session_id=? AND turn_index=?",
            (session_id, turn_index),
        )
        return dict(rows[0]) if rows else None

    def get_latest_completed_turn(self, session_id: str) -> dict[str, Any] | None:
        """Return the newest settled turn with fixed query cost."""
        rows = self.execute_read(
            "SELECT turn_index, response_text, stop_reason, started_at, completed_at "
            "FROM turns WHERE session_id=? AND completed_at IS NOT NULL "
            "ORDER BY turn_index DESC LIMIT 1",
            (session_id,),
        )
        return dict(rows[0]) if rows else None

    def append_event(
        self,
        session_id: str,
        event_id: int,
        event_type: str,
        data: dict[str, Any],
        timestamp: float,
    ) -> None:
        self.start_writer()
        self._event_write_q.put((
            session_id, event_id, event_type, json.dumps(data), timestamp,
        ))

    def get_events(
        self, session_id: str, after: int = 0
    ) -> list[dict[str, Any]]:
        self.flush()
        rows = self.execute_read(
            "SELECT * FROM events WHERE session_id=? AND event_id>? ORDER BY event_id",
            (session_id, after),
        )
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            result.append(d)
        return result

    def get_max_event_id(self, session_id: str) -> int:
        self.flush()
        rows = self.execute_read(
            "SELECT MAX(event_id) as max_id FROM events WHERE session_id=?",
            (session_id,),
        )
        val = rows[0]["max_id"] if rows else None
        return val or 0

    def get_events_range(
        self, session_id: str, start_id: int, end_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Return events with start_id <= event_id <= end_id (inclusive).

        Used for random-access historical reads. Does not touch any
        delivery cursor. ``end_id=None`` means "to the latest event".
        """
        self.flush()
        if end_id is None:
            rows = self.execute_read(
                "SELECT * FROM events WHERE session_id=? AND event_id>=? "
                "ORDER BY event_id",
                (session_id, start_id),
            )
        else:
            rows = self.execute_read(
                "SELECT * FROM events WHERE session_id=? AND event_id>=? "
                "AND event_id<=? ORDER BY event_id",
                (session_id, start_id, end_id),
            )
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            result.append(d)
        return result

    def get_cursor(self, caller_id: str, session_id: str) -> int:
        """Return the last-acked event id for a caller on a session (0 if none)."""
        rows = self.execute_read(
            "SELECT last_acked_id FROM delivery_cursors "
            "WHERE caller_id=? AND session_id=?",
            (caller_id, session_id),
        )
        return rows[0]["last_acked_id"] if rows else 0

    def get_cursor_state(
        self, caller_id: str, session_id: str
    ) -> dict[str, Any]:
        """Return durable cursor position plus any pending log invalidation."""
        rows = self.execute_read(
            "SELECT last_acked_id, updated_at FROM delivery_cursors "
            "WHERE caller_id=? AND session_id=?",
            (caller_id, session_id),
        )
        invalidations = self.execute_read(
            "SELECT prior_last_acked_id, prior_head_id, "
            "prior_continuity_id, current_continuity_id, invalidated_at "
            "FROM delivery_cursor_invalidations "
            "WHERE caller_id=? AND session_id=?",
            (caller_id, session_id),
        )
        return {
            "registered": bool(rows),
            "last_acked_id": rows[0]["last_acked_id"] if rows else 0,
            "updated_at": rows[0]["updated_at"] if rows else None,
            "invalidation": (
                dict(invalidations[0]) if invalidations else None
            ),
        }

    @staticmethod
    def _event_continuity(session_id: str, origin: float | None) -> str | None:
        if origin is None:
            return None
        value = f"{session_id}:{origin!r}".encode()
        return hashlib.sha256(value).hexdigest()[:16]

    def get_controlled_cursor_state(
        self, caller_id: str, session_id: str
    ) -> dict[str, Any]:
        """Read cursor, invalidation, head, and continuity from one DB snapshot."""
        self.flush()
        conn = self._get_conn()
        while True:
            conn.execute("BEGIN")
            try:
                row = conn.execute(
                    "SELECT last_acked_id, updated_at FROM delivery_cursors "
                    "WHERE caller_id=? AND session_id=?",
                    (caller_id, session_id),
                ).fetchone()
                invalidation = conn.execute(
                    "SELECT prior_last_acked_id, prior_head_id, "
                    "prior_continuity_id, current_continuity_id, invalidated_at "
                    "FROM delivery_cursor_invalidations "
                    "WHERE caller_id=? AND session_id=?",
                    (caller_id, session_id),
                ).fetchone()
                head_row = conn.execute(
                    "SELECT MAX(event_id) AS head_id, "
                    "MIN(CASE WHEN event_id=1 THEN timestamp END) AS origin "
                    "FROM events WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                origin = head_row["origin"] if head_row else None
                continuity_id = self._event_continuity(session_id, origin)
                if (
                    invalidation is not None
                    and invalidation["current_continuity_id"] is None
                    and continuity_id is not None
                ):
                    conn.rollback()
                    with self._write_lock:
                        conn.execute("BEGIN IMMEDIATE")
                        try:
                            repair_head = conn.execute(
                                "SELECT MIN(CASE WHEN event_id=1 "
                                "THEN timestamp END) AS origin "
                                "FROM events WHERE session_id=?",
                                (session_id,),
                            ).fetchone()
                            repair_continuity = self._event_continuity(
                                session_id,
                                (
                                    repair_head["origin"]
                                    if repair_head
                                    else None
                                ),
                            )
                            if repair_continuity is not None:
                                conn.execute(
                                    "UPDATE delivery_cursor_invalidations SET "
                                    "current_continuity_id=? "
                                    "WHERE caller_id=? AND session_id=? "
                                    "AND current_continuity_id IS NULL",
                                    (
                                        repair_continuity,
                                        caller_id,
                                        session_id,
                                    ),
                                )
                            conn.commit()
                        except BaseException:
                            conn.rollback()
                            raise
                    continue
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            break
        return {
            "registered": row is not None,
            "last_acked_id": row["last_acked_id"] if row else 0,
            "updated_at": row["updated_at"] if row else None,
            "head_id": (head_row["head_id"] if head_row else None) or 0,
            "continuity_id": continuity_id,
            "invalidation": dict(invalidation) if invalidation else None,
        }

    def acknowledge_controlled_cursor(
        self,
        caller_id: str,
        session_id: str,
        last_acked_id: int,
        timestamp: float,
        *,
        continuity_id: str | None,
    ) -> dict[str, Any]:
        """Conditionally ACK one event-log generation in a single transaction."""
        self.flush()
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                invalidation = conn.execute(
                    "SELECT prior_last_acked_id, prior_head_id, "
                    "prior_continuity_id, current_continuity_id, invalidated_at "
                    "FROM delivery_cursor_invalidations "
                    "WHERE caller_id=? AND session_id=?",
                    (caller_id, session_id),
                ).fetchone()
                head_row = conn.execute(
                    "SELECT MAX(event_id) AS head_id, "
                    "MIN(CASE WHEN event_id=1 THEN timestamp END) AS origin "
                    "FROM events WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                head_id = (head_row["head_id"] if head_row else None) or 0
                current_continuity = self._event_continuity(
                    session_id, head_row["origin"] if head_row else None
                )
                if (
                    invalidation is not None
                    and invalidation["current_continuity_id"] is None
                    and current_continuity is not None
                ):
                    conn.execute(
                        "UPDATE delivery_cursor_invalidations SET "
                        "current_continuity_id=? "
                        "WHERE caller_id=? AND session_id=? "
                        "AND current_continuity_id IS NULL",
                        (current_continuity, caller_id, session_id),
                    )
                    invalidation = conn.execute(
                        "SELECT prior_last_acked_id, prior_head_id, "
                        "prior_continuity_id, current_continuity_id, "
                        "invalidated_at FROM delivery_cursor_invalidations "
                        "WHERE caller_id=? AND session_id=?",
                        (caller_id, session_id),
                    ).fetchone()
                if invalidation is not None and (
                    invalidation["current_continuity_id"] is None
                    or continuity_id
                    != invalidation["current_continuity_id"]
                ):
                    conn.rollback()
                    return {
                        "accepted": False,
                        "code": "cursor_invalidated",
                        "continuity_id": current_continuity,
                        "invalidation": dict(invalidation),
                        "head_id": head_id,
                    }
                if (
                    continuity_id is not None
                    and continuity_id != current_continuity
                ):
                    conn.rollback()
                    return {
                        "accepted": False,
                        "code": "cursor_invalidated",
                        "continuity_id": current_continuity,
                        "invalidation": (
                            dict(invalidation) if invalidation else None
                        ),
                        "head_id": head_id,
                    }
                if last_acked_id > head_id:
                    conn.rollback()
                    return {
                        "accepted": False,
                        "code": "replay_gap",
                        "continuity_id": current_continuity,
                        "invalidation": (
                            dict(invalidation) if invalidation else None
                        ),
                        "head_id": head_id,
                    }
                row = conn.execute(
                    "SELECT last_acked_id FROM delivery_cursors "
                    "WHERE caller_id=? AND session_id=?",
                    (caller_id, session_id),
                ).fetchone()
                current = row["last_acked_id"] if row else 0
                effective = (
                    last_acked_id
                    if current > head_id
                    else max(current, last_acked_id)
                )
                conn.execute(
                    "INSERT INTO delivery_cursors "
                    "(caller_id, session_id, last_acked_id, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(caller_id, session_id) DO UPDATE SET "
                    "last_acked_id=excluded.last_acked_id, "
                    "updated_at=excluded.updated_at",
                    (caller_id, session_id, effective, timestamp),
                )
                if invalidation is not None:
                    conn.execute(
                        "DELETE FROM delivery_cursor_invalidations "
                        "WHERE caller_id=? AND session_id=? "
                        "AND current_continuity_id=?",
                        (caller_id, session_id, continuity_id),
                    )
                conn.commit()
                return {
                    "accepted": True,
                    "last_acked_id": effective,
                    "continuity_id": current_continuity,
                    "head_id": head_id,
                    "invalidation": None,
                }
            except BaseException:
                conn.rollback()
                raise

    def ensure_cursor(
        self, caller_id: str, session_id: str, timestamp: float
    ) -> int:
        """Register a zero-position consumer without clearing invalidation."""
        self.execute_write(
            "INSERT INTO delivery_cursors "
            "(caller_id, session_id, last_acked_id, updated_at) "
            "VALUES (?, ?, 0, ?) "
            "ON CONFLICT(caller_id, session_id) DO NOTHING",
            (caller_id, session_id, timestamp),
        )
        return self.get_cursor(caller_id, session_id)

    def set_cursor(
        self, caller_id: str, session_id: str, last_acked_id: int, timestamp: float
    ) -> int:
        """Advance a caller's delivery cursor, monotonically.

        The stored value never regresses: a smaller or duplicate ack is
        ignored. Returns the cursor value in effect after the call.
        """
        with self._write_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT last_acked_id FROM delivery_cursors "
                "WHERE caller_id=? AND session_id=?",
                (caller_id, session_id),
            ).fetchone()
            current = row["last_acked_id"] if row else 0
            new_val = max(current, last_acked_id)
            conn.execute(
                "INSERT INTO delivery_cursors "
                "(caller_id, session_id, last_acked_id, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(caller_id, session_id) DO UPDATE SET "
                "last_acked_id=excluded.last_acked_id, updated_at=excluded.updated_at",
                (caller_id, session_id, new_val, timestamp),
            )
            conn.execute(
                "DELETE FROM delivery_cursor_invalidations "
                "WHERE caller_id=? AND session_id=?",
                (caller_id, session_id),
            )
            conn.commit()
            return new_val

    def reset_delivery_cursors(
        self,
        session_id: str,
        *,
        prior_head_id: int = 0,
        prior_continuity_id: str | None = None,
        current_continuity_id: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Drop all delivery cursors for a session (get_cursor -> 0 afterwards).

        Called when a session's event log is **rebuilt** (resync replaces the
        log with the agent's authoritative replay, renumbering event ids). The
        cursors are monotonic and would otherwise point past the rebuilt log --
        orphaning consumers. Before resetting, preserve one explicit
        invalidation record per registered caller so reconnecting proxy
        subscribers can force full reconciliation instead of translating the
        stale cursor into an empty or silently restarted stream.
        """
        with self._write_lock:
            conn = self._get_conn()
            invalidated_at = time.time() if timestamp is None else timestamp
            conn.execute(
                "INSERT INTO delivery_cursor_invalidations "
                "(caller_id, session_id, prior_last_acked_id, prior_head_id, "
                "prior_continuity_id, current_continuity_id, invalidated_at) "
                "SELECT caller_id, session_id, last_acked_id, ?, ?, ?, ? "
                "FROM delivery_cursors WHERE session_id=? "
                "ON CONFLICT(caller_id, session_id) DO UPDATE SET "
                "prior_last_acked_id=excluded.prior_last_acked_id, "
                "prior_head_id=excluded.prior_head_id, "
                "prior_continuity_id=excluded.prior_continuity_id, "
                "current_continuity_id=excluded.current_continuity_id, "
                "invalidated_at=excluded.invalidated_at",
                (
                    prior_head_id,
                    prior_continuity_id,
                    current_continuity_id,
                    invalidated_at,
                    session_id,
                ),
            )
            conn.execute(
                "UPDATE delivery_cursor_invalidations SET "
                "current_continuity_id=?, invalidated_at=? "
                "WHERE session_id=?",
                (current_continuity_id, invalidated_at, session_id),
            )
            conn.execute(
                "DELETE FROM delivery_cursors WHERE session_id=?", (session_id,)
            )
            conn.commit()

    def update_delivery_cursor_invalidation_continuity(
        self,
        session_id: str,
        continuity_id: str,
        *,
        timestamp: float | None = None,
    ) -> None:
        """Fill the replacement epoch after an empty rebuild gets its first event."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE delivery_cursor_invalidations SET "
                "current_continuity_id=?, invalidated_at=? "
                "WHERE session_id=? AND current_continuity_id IS NULL",
                (
                    continuity_id,
                    time.time() if timestamp is None else timestamp,
                    session_id,
                ),
            )
            conn.commit()
