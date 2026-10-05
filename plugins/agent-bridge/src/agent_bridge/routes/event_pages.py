"""JSON pages of a session's durable event log (non-streaming reads)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def rows_to_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert durable event rows to the wire event-dict shape."""
    return [
        {"id": r["event_id"], "event": r["event_type"], "data": r["data"],
         "timestamp": r["timestamp"]}
        for r in rows
    ]


def events_before_page(
    db: Any, session_id: str, before: int, limit: int, streaming_args: bool,
) -> dict[str, Any]:
    """Backward page: the newest ``limit`` events with ``id < before``.

    Events are returned in ascending id order; ``has_more`` reports whether
    older events remain (page further back with ``before=<events[0].id>``).
    The page touches no delivery cursor. ``streaming_args`` is true when the
    request also carried stream-only parameters (``after`` / ``controlled`` /
    ``transient``), which cannot be combined with ``before``.
    """
    if streaming_args:
        raise HTTPException(
            status_code=422,
            detail="before cannot be combined with after, controlled, or transient",
        )
    rows = db.get_events_before(session_id, before, limit + 1)
    has_more = len(rows) > limit
    if has_more:
        rows = rows[1:]
    return {"session_id": session_id, "events": rows_to_events(rows), "has_more": has_more}
