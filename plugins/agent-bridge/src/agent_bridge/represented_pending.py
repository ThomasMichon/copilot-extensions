"""Open parent requests on a represented session's observed event tail.

A represented history is the event tail the interactive CLI pushed. A parent
question or permission request in it stays open until the parent turn completes
or its own tool call starts or updates. The represented result snapshot reports
the newest open one, and current attention names the event behind the reason
the snapshot derives.
"""

from __future__ import annotations

from typing import Any

from .events import is_nested_event_data


def represented_pending_events(events: list[Any]) -> list[Any]:
    """The parent questions and permission requests still open after the last
    parent turn completion: unkeyed ones, then keyed ones in first-seen order.
    A snapshot reports the last one."""
    last_complete = max(
        (
            index
            for index, event in enumerate(events)
            if event.event == "turn_complete"
            and not is_nested_event_data(event.data)
        ),
        default=-1,
    )
    pending_by_id: dict[str, Any] = {}
    unkeyed: list[Any] = []
    for event in events[last_complete + 1:]:
        if is_nested_event_data(event.data):
            continue
        tool_call_id = (
            event.data.get("tool_call_id") or event.data.get("toolCallId")
        )
        key = str(tool_call_id) if tool_call_id else ""
        if event.event in {"ask_user_request", "permission_request"}:
            if key:
                pending_by_id[key] = event
            else:
                unkeyed.append(event)
        elif event.event in {"tool_call_start", "tool_call_update"} and key:
            pending_by_id.pop(key, None)
    return [*unkeyed, *pending_by_id.values()]


def represented_boundary_event(events: list[Any], reason: str | None) -> Any | None:
    """The event behind *reason*, the attention a represented result snapshot
    derives from *events*: the open question or permission request it reports,
    or the parent turn completion. ``None`` when *events* hold no such event
    (``ended`` never has one)."""
    if reason in {"input_required", "permission_required"}:
        pending = represented_pending_events(events)
        wanted = (
            "permission_request" if reason == "permission_required" else "ask_user_request"
        )
        if pending and pending[-1].event == wanted:
            return pending[-1]
        return None
    if reason == "turn_complete":
        return next(
            (
                event
                for event in reversed(events)
                if event.event == "turn_complete"
                and not is_nested_event_data(event.data)
            ),
            None,
        )
    return None
