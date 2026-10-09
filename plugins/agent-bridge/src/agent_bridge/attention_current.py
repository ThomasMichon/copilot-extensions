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
is derived from the event tail the interactive CLI pushed (reduced fidelity).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .attention_wait import _event_reason
from .models import AttentionReason, SessionStatus

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
    #: when its request predates this daemon generation; ``None`` with no reason.
    availability: Literal["available", "unknown_after_restart"] | None = None
    fidelity: Literal["full", "reduced"]
    #: One line about the request (its message or intention), when there is one.
    detail: str | None = None


def _one_line(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:_DETAIL_MAX] or None


def _request_detail(event_type: str, data: dict[str, Any]) -> str | None:
    if event_type == "ask_user_request":
        return _one_line(data.get("message"))
    if event_type == "permission_request":
        return _one_line(data.get("intention") or data.get("kind"))
    return _one_line(data.get("message") or data.get("reason"))


def _request_is_live(session: Any, family: str, correlation_id: str) -> bool:
    client = getattr(session, "client", None)
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
    _continuity, events = session.event_log.snapshot_history(durable=True)
    open_requests: dict[tuple[str, str], tuple[AttentionReason, str | None]] = {}
    settled: AttentionReason | None = None
    for event in events:
        kind, data = event.event, event.data if isinstance(event.data, dict) else {}
        if kind in _REQUESTS:
            family, key, reason = _REQUESTS[kind]
            correlation_id = str(data.get(key) or "")
            if correlation_id:
                open_requests.pop((family, correlation_id), None)  # newest last
                open_requests[(family, correlation_id)] = (reason, _request_detail(kind, data))
            continue
        if kind in _RESOLUTIONS:
            family, key = _RESOLUTIONS[kind]
            open_requests.pop((family, str(data.get(key) or "")), None)
            continue
        if kind == "user_message":
            settled = None  # a new turn: the last one's boundary is no longer current
        if kind in _TURN_ENDS:
            open_requests.clear()
        reason = _event_reason({"event_type": kind, "data": data})
        if reason is not None and reason not in (
            AttentionReason.INPUT_REQUIRED,
            AttentionReason.PERMISSION_REQUIRED,
            AttentionReason.POLICY_REQUIRED,
        ):
            settled = reason
    base = {
        "requested_ref": requested_ref,
        "registry": "bridge",
        "session_id": session.session_id,
        "worktree_id": getattr(session.target, "worktree_id", None),
        "fidelity": "full",
    }
    if open_requests:
        (family, correlation_id), (reason, detail) = list(open_requests.items())[-1]
        live = _request_is_live(session, family, correlation_id)
        return CurrentAttention(
            **base, reason=reason, detail=detail,
            availability="available" if live else "unknown_after_restart",
        )
    status = session.status
    if status in _TERMINAL:
        return CurrentAttention(**base, reason=_TERMINAL[status], availability="available")
    if status == SessionStatus.IDLE and settled is not None:
        return CurrentAttention(**base, reason=settled, availability="available")
    if settled == AttentionReason.UNREACHABLE:
        return CurrentAttention(**base, reason=settled, availability="available")
    return CurrentAttention(**base)


def current_represented_attention(snapshot: Any, registration: dict[str, Any],
                                  requested_ref: str) -> CurrentAttention:
    """The current reason of a represented session, from its result snapshot."""
    value = snapshot.state.attention.value
    detail = None
    pending = snapshot.state.pending_input.value
    if isinstance(pending, list) and pending and isinstance(pending[0], dict):
        detail = _one_line(pending[0].get("message"))
    try:
        reason = AttentionReason(value) if value else None
    except ValueError:
        reason = None
    return CurrentAttention(
        requested_ref=requested_ref, registry="live", session_id=str(registration["session_id"]),
        worktree_id=registration.get("worktree_id"), reason=reason,
        availability="available" if reason else None, fidelity="reduced",
        detail=detail if reason else None,
    )
