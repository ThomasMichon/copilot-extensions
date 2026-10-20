"""The *current* attention reason of one session: a bounded read, never a wait.

``wait --attention`` settles on the earliest selected boundary after a position;
an operator's inbox needs the other question -- what is this session parked on
right now? -- for a bridge-managed (owned) session and for a registered
interactive (represented) one alike.

Owned sessions are read from their durable history: an ``ask_user``,
``permission`` or ``policy`` request is open until its own resolution event (or
the turn it belongs to ends), and the newest open request wins. A request this
daemon generation can no longer answer reads ``unknown_after_restart``, like
``wait --attention``'s request availability. With nothing open, a terminal
session status, or the latest settled boundary of an idle session, is the
reason; a running session with nothing open has none (``null``).

Represented sessions reuse the represented result snapshot's attention, which
is derived from the event tail the interactive CLI pushed (reduced fidelity);
its boundary is read from the same captured window of that tail.

Since HTTP protocol 42 a reason also names its boundary: ``since`` is when the
event that produced it was recorded and ``position`` is that event's opaque
attention position -- for an owned session the same position ``wait
--attention`` accepts and returns, so an unchanged position is the same
boundary. Both are ``None`` when the bridge holds no such event (nothing
pending, a terminal status with no recorded transition, or a represented
history this daemon generation hasn't seen). Positions compare only for the same
target. A represented position is a comparison key only (waiting stays
owned-only), and a represented boundary this generation learned from a history
replay keeps its ``since`` but has no position: the replay renumbered it, so its
new position would read as a new boundary.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from .attention_wait import _event_reason, _input_correlation, _input_is_live, _position_token
from .models import AttentionReason, SessionStatus
from .represented_pending import represented_boundary_event

#: Request events: ``(family, correlation key, reason)``.
_REQUESTS = {
    "ask_user_request": ("input", "tool_call_id", AttentionReason.INPUT_REQUIRED),
    "permission_request": ("permission", "request_id", AttentionReason.PERMISSION_REQUIRED),
    "policy_required": ("policy", "action_id", AttentionReason.POLICY_REQUIRED),
}
#: Resolution events: ``(family, correlation key)``.
_RESOLUTIONS = {
    "ask_user_resolved": ("input", "tool_call_id"),
    "ask_user_withdrawn": ("input", "tool_call_id"),
    "permission_resolved": ("permission", "request_id"),
}
#: A request can't outlive its turn, or a handoff to a successor session.
_TURN_ENDS = frozenset({"turn_complete", "session_handoff"})
_TERMINAL = {
    SessionStatus.FAILED: AttentionReason.FAILED,
    SessionStatus.STOPPED: AttentionReason.STOPPED,
    SessionStatus.ENDED: AttentionReason.ENDED,
}
_DETAIL_MAX = 200


class CurrentAttention(BaseModel):
    """``GET /api/v1/sessions/{ref}/attention/current``."""

    requested_ref: str
    registry: Literal["bridge", "live"]
    session_id: str
    worktree_id: str | None = None
    reason: AttentionReason | None = None
    #: ``available`` when the reason can be acted on now; ``unknown_after_restart``
    #: when its request (or, for a represented session, its whole history)
    #: predates this daemon generation, so the reason can't be known; ``None``
    #: with no reason.
    availability: Literal["available", "unknown_after_restart"] | None = None
    fidelity: Literal["full", "reduced"]
    #: One line about the request (its message or intention), when there is one.
    detail: str | None = None
    #: When the boundary event behind ``reason`` was recorded (RFC 3339), or
    #: ``None`` when the bridge holds no such event (protocol 42).
    since: str | None = None
    #: The opaque attention position of that boundary, or ``None`` (protocol 42).
    #: Compare it only for the same target; only an owned session's waits.
    position: str | None = None


def _one_line(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:_DETAIL_MAX] or None


def _rfc3339(timestamp: Any) -> str | None:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    if not math.isfinite(timestamp):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _boundary(event: Any, continuity: str | None, *, worktree_id: str | None,
              session_id: str) -> dict[str, str | None]:
    """``since`` and ``position`` of the boundary *event*; no continuity, no position."""
    if event is None:
        return {"since": None, "position": None}
    position = _position_token(
        logical_delegate_kind="worktree" if worktree_id else "session",
        logical_delegate_id=str(worktree_id or session_id),
        observed_session_id=session_id,
        continuity=continuity,
        event_id=int(event.id),
    ) if continuity else None
    return {"since": _rfc3339(event.timestamp), "position": position}


def _request_detail(event_type: str, data: dict[str, Any]) -> str | None:
    if event_type == "ask_user_request":
        return _one_line(data.get("message"))
    if event_type == "permission_request":
        return _one_line(data.get("intention") or data.get("kind"))
    return _one_line(data.get("message") or data.get("reason"))


def _request_is_live(
    session: Any, family: str, correlation_id: str, correlation_key: str,
) -> bool:
    client = getattr(session, "client", None)
    if family == "input":
        return _input_is_live(client, correlation_key, correlation_id)
    reader = {
        "input": getattr(client, "has_pending_elicitation", None),
        "permission": getattr(client, "has_pending_permission", None),
    }.get(family)
    if family == "policy":
        # A policy decision is the daemon's own gate: it never depends on the ACP client.
        return True
    return bool(callable(reader) and reader(correlation_id))


def current_owned_attention(session: Any, requested_ref: str) -> CurrentAttention:
    """The current reason of an owned session (see the module docstring)."""
    continuity, events = session.event_log.snapshot_history(durable=True)
    open_requests: dict[tuple[str, str, str], tuple[AttentionReason, str | None, Any]] = {}
    settled: AttentionReason | None = None
    settled_event: Any = None
    #: The latest recorded status transition: ``(its reason, the event)``.
    last_state: tuple[AttentionReason | None, Any] = (None, None)
    for event in events:
        kind, data = event.event, event.data if isinstance(event.data, dict) else {}
        if kind in _REQUESTS:
            family, key, reason = _REQUESTS[kind]
            if family == "input":
                key, correlation_id = _input_correlation(data)
            else:
                correlation_id = str(data.get(key) or "")
            if correlation_id:
                open_requests.pop((family, key, correlation_id), None)  # newest last
                open_requests[(family, key, correlation_id)] = (
                    reason, _request_detail(kind, data), event)
            continue
        if kind in _RESOLUTIONS:
            family, key = _RESOLUTIONS[kind]
            if family == "input":
                key, correlation_id = _input_correlation(data)
            else:
                correlation_id = str(data.get(key) or "")
            open_requests.pop((family, key, correlation_id), None)
            continue
        if kind == "user_message":
            # A new turn: the last one's boundary is no longer current.
            settled = settled_event = None
        if kind in _TURN_ENDS:
            open_requests.clear()
        reason = _event_reason({"event_type": kind, "data": data})
        if kind == "session_state_changed":
            last_state = (reason, event)
        if reason is not None and reason not in (
            AttentionReason.INPUT_REQUIRED,
            AttentionReason.PERMISSION_REQUIRED,
            AttentionReason.POLICY_REQUIRED,
        ):
            settled, settled_event = reason, event
    worktree_id = getattr(session.target, "worktree_id", None)
    base = {
        "requested_ref": requested_ref,
        "registry": "bridge",
        "session_id": session.session_id,
        "worktree_id": worktree_id,
        "fidelity": "full",
    }

    def boundary(event: Any) -> dict[str, str | None]:
        return _boundary(event, continuity, worktree_id=worktree_id, session_id=session.session_id)

    status = session.status
    if status in _TERMINAL:
        # Its boundary is the latest recorded status transition, when that is into this status.
        transition = last_state[1] if last_state[0] == _TERMINAL[status] else None
        return CurrentAttention(**base, reason=_TERMINAL[status], availability="available",
                                **boundary(transition))
    if open_requests:
        (family, key, correlation_id), (reason, detail, event) = list(open_requests.items())[-1]
        live = _request_is_live(session, family, correlation_id, key)
        return CurrentAttention(
            **base, reason=reason, detail=detail,
            availability="available" if live else "unknown_after_restart",
            **boundary(event),
        )
    if status == SessionStatus.IDLE and settled is not None:
        return CurrentAttention(**base, reason=settled, availability="available",
                                **boundary(settled_event))
    if settled == AttentionReason.UNREACHABLE:
        return CurrentAttention(**base, reason=settled, availability="available",
                                **boundary(settled_event))
    return CurrentAttention(**base)


def current_represented_attention(snapshot: Any, registration: dict[str, Any],
                                  requested_ref: str, *,
                                  tail: tuple[str | None, list[Any]] | None = None,
                                  replayed_before: float | None = None) -> CurrentAttention:
    """The current reason of a represented session, from its result snapshot.

    With *tail* -- the ``(continuity, events)`` window the snapshot was built
    from -- the reason also names its boundary, read from that same window so
    the detail and the position name the same event. A request that resolves,
    or a newer one that arrives, after the window was captured is the next
    read's. An event older than *replayed_before* (this daemon generation's
    start) came from a history replay and gets no position.
    """
    value = snapshot.state.attention.value
    detail = None
    pending = snapshot.state.pending_input.value
    if isinstance(pending, list) and pending and isinstance(pending[0], dict):
        detail = _one_line(pending[0].get("message"))
    try:
        reason = AttentionReason(value) if value else None
    except ValueError:
        reason = None
    session_id = str(registration["session_id"])
    worktree_id = registration.get("worktree_id")
    found: dict[str, str | None] = {"since": None, "position": None}
    if reason is not None and tail is not None:
        continuity, events = tail
        event = represented_boundary_event(events, reason.value)
        replayed = (event is not None and replayed_before is not None
                    and event.timestamp < replayed_before)
        found = _boundary(event, None if replayed else continuity,
                          worktree_id=worktree_id, session_id=session_id)
    return CurrentAttention(
        requested_ref=requested_ref, registry="live", session_id=session_id,
        worktree_id=worktree_id, reason=reason,
        availability="available" if reason else None, fidelity="reduced",
        detail=detail if reason else None, **found,
    )
