"""JSON pages of a session's durable event log (non-streaming reads)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 1000


def rows_to_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert durable event rows to the wire event-dict shape."""
    return [
        {"id": r["event_id"], "event": r["event_type"], "data": r["data"],
         "timestamp": r["timestamp"]}
        for r in rows
    ]


def events_before_page(
    session: Any,
    db: Any,
    before: int,
    limit: int,
    streaming_args: bool,
    continuity_id: str | None = None,
) -> dict[str, Any]:
    """Backward page: the newest ``limit`` events with ``id < before``.

    Events are returned in ascending id order; ``has_more`` reports whether
    older events remain (page further back with ``before=<events[0].id>``).
    ``continuity_id`` names the event-log generation the ids belong to; a
    caller passes it back with the next ``before`` and gets 409
    ``cursor_invalidated`` if a resync rebuilt the log in between (the ids no
    longer address the same history, so paging must restart from the tail).
    The page touches no delivery cursor. ``streaming_args`` is true when the
    request also carried stream-only parameters (``after`` / ``controlled`` /
    ``transient``), which cannot be combined with ``before``.
    """
    if streaming_args:
        raise HTTPException(
            status_code=422,
            detail="before cannot be combined with after, controlled, or transient",
        )
    if not isinstance(limit, int):  # direct callers see unresolved Query defaults
        limit = DEFAULT_PAGE_LIMIT
    if not isinstance(continuity_id, str):
        continuity_id = None
    session_id = session.session_id
    event_log = getattr(session, "event_log", None)
    if event_log is not None:
        # One atomic snapshot of the in-memory log: rebuild() replaces it under
        # the same lock, so the page, has_more, and continuity agree.
        current, snapshot, has_more = event_log.snapshot_before(before, limit)
        events = [
            {"id": e.id, "event": e.event, "data": e.data, "timestamp": e.timestamp}
            for e in snapshot
        ]
    else:
        # No live log means nothing can rebuild this session's history.
        current = None
        rows = db.get_events_before(session_id, before, limit + 1)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[1:]
        events = rows_to_events(rows)
    if continuity_id is not None and continuity_id != current:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cursor_invalidated",
                "message": "the event log was rebuilt; restart paging from the tail",
                "action": "full_reconcile",
                "prior_continuity_id": continuity_id,
                "continuity_id": current,
            },
        )
    return {
        "session_id": session_id,
        "continuity_id": current,
        "events": events,
        "has_more": has_more,
    }
