"""Rebuild a represented session's history after a bridge restart.

A represented session's event log lives in this daemon's memory only
(:class:`~agent_bridge.live_representation.LiveEventStore`), while its
registration is durable: after a restart the registration survives and the log
is gone, so a viewer shows nothing (an idle session has nothing new to send)
and a reader can't tell what it last did. The session's own transcript is the
durable history, and only the session's extension can read it -- it runs where
the CLI runs (on this machine, or in a CodeSpace).

So for a session registered in an earlier daemon generation that has no log in
this one, the bridge asks its extension to replay the transcript's tail (the
``control:replay-history`` session control) and holds the session's incoming
live events meanwhile. The replay lands first, then the held events, so the
rebuilt log stays in order; the store's per-event-id dedup drops the overlap.
An extension that can't replay (it predates the control, or the transcript is
unreadable) rejects or ignores it: after :data:`REPLAY_WAIT_SECONDS` the held
events are released and a late replay is ignored -- it would land out of order.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

#: The ``live_messages.kind`` of a history replay request (a session control).
REPLAY_HISTORY_CONTROL = "control:replay-history"
#: How long a session's live events are held for its replay.
REPLAY_WAIT_SECONDS = 60.0
#: This daemon generation's start: a session registered before it lost its log.
GENERATION_STARTED_AT = time.time()


class HistoryBackfill:
    """Sessions awaiting their history replay in this daemon generation."""

    def __init__(self, started_at: float = GENERATION_STARTED_AT) -> None:
        self.started_at = started_at
        self._pending: dict[str, dict[str, Any]] = {}
        self._done: set[str] = set()
        self._lock = Lock()

    def maybe_start(self, db: Any, store: Any, row: dict[str, Any], now: float) -> bool:
        """Ask *row*'s extension for its history when it predates this
        generation and has no log in it; once per session per generation."""
        sid = str(row.get("session_id") or "")
        registered = row.get("registered_at")
        if (not sid or not isinstance(registered, (int, float)) or registered >= self.started_at
                or (row.get("status") or "live") != "live" or store.get(sid) is not None):
            return False
        with self._lock:
            if sid in self._done or sid in self._pending:
                return False
            self._pending[sid] = {"deadline": now + REPLAY_WAIT_SECONDS, "held": []}
        _control_id, reason = db.enqueue_live_message_if_fresh(
            sid, sender="agent-bridge", body="", now=now, kind=REPLAY_HISTORY_CONTROL, delivery="queue")
        if reason is not None:
            with self._lock:  # nothing to wait for: never hold its events
                self._pending.pop(sid, None)
                self._done.add(sid)
            return False
        return True

    def release_expired(self, store: Any, now: float, worktree_of: Any = lambda sid: None) -> None:
        """Release the held events of every replay that didn't arrive in time."""
        with self._lock:
            expired = [(sid, p) for sid, p in self._pending.items() if now >= p["deadline"]]
            for sid, _p in expired:
                self._pending.pop(sid, None)
                self._done.add(sid)
        for sid, p in expired:
            for batch in p["held"]:
                store.ingest(sid, batch, worktree_id=worktree_of(sid))

    def ingest(self, store: Any, session_id: str, raw: list[dict[str, Any]], *,
               worktree_id: str | None) -> int:
        """Ingest a live batch, or hold it while the session's replay is awaited."""
        with self._lock:
            pending = self._pending.get(session_id)
            if pending is not None:
                pending["held"].append(raw)
                return 0
        return store.ingest(session_id, raw, worktree_id=worktree_id)

    def replay(self, store: Any, session_id: str, raw: list[dict[str, Any]], *,
               worktree_id: str | None) -> int:
        """Land a replayed history, then the live events held for it. A replay
        nothing awaits (late, or unsolicited) is ignored: it would land out of
        order after newer events."""
        with self._lock:
            pending = self._pending.pop(session_id, None)
            if pending is None:
                return 0
            self._done.add(session_id)
        count = store.ingest(session_id, raw, worktree_id=worktree_id)
        for batch in pending["held"]:
            count += store.ingest(session_id, batch, worktree_id=worktree_id)
        return count


def backfill(app_state: Any) -> HistoryBackfill:
    """The app's backfill registry (created on first use)."""
    found = getattr(app_state, "history_backfill", None)
    if found is None:
        found = app_state.history_backfill = HistoryBackfill()
    return found


def on_registration(request: Any, row: dict[str, Any] | None) -> None:
    """From a registration (a heartbeat): release overdue holds, and ask a
    session that lost its log in a restart for its history."""
    store = getattr(request.app.state, "live_event_store", None)
    if store is None or not row:
        return
    bf, now = backfill(request.app.state), time.time()
    db = request.app.state.db
    bf.release_expired(store, now, lambda sid: (db.get_live_session(sid) or {}).get("worktree_id"))
    bf.maybe_start(db, store, row, now)


def latest_id(store: Any, session_id: str) -> int:
    log = store.get(session_id)
    return log.latest_id if log is not None else 0


def ingest(request: Any, registration: dict[str, Any], raw: list[dict[str, Any]], *, replay: bool) -> int:
    """The ingest route's store write (see the module docstring)."""
    store = request.app.state.live_event_store
    db = request.app.state.db
    bf, now, sid = backfill(request.app.state), time.time(), registration["session_id"]
    wid = registration.get("worktree_id")
    bf.release_expired(store, now, lambda s: (db.get_live_session(s) or {}).get("worktree_id"))
    if replay:
        return bf.replay(store, sid, raw, worktree_id=wid)
    bf.maybe_start(db, store, registration, now)
    return bf.ingest(store, sid, raw, worktree_id=wid)
