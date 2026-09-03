"""Stateless, cursor-neutral attention-boundary evaluation."""

from __future__ import annotations

import base64
import binascii
import json
from typing import TYPE_CHECKING, Any, Iterable

from .models import (
    AttentionIdentity,
    AttentionReason,
    AttentionReference,
    AttentionWaitResponse,
)

if TYPE_CHECKING:
    from .db import Database
    from .session_manager import Session


_TOKEN_PREFIX = "aba1."
_MAX_TOKEN_CHARS = 2048
_CANCELLED_STOP_REASONS = frozenset({"cancelled", "canceled"})


class AttentionTokenError(ValueError):
    """An opaque attention position is malformed or targets another delegate."""


class AttentionHistoryChangedError(AttentionTokenError):
    """An attention position names an event-log history that was rebuilt."""


def _encode_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"v": 1, **payload}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_token(token: str) -> dict[str, Any]:
    if (
        not token
        or len(token) > _MAX_TOKEN_CHARS
        or not token.startswith(_TOKEN_PREFIX)
    ):
        raise AttentionTokenError("invalid attention position")
    encoded = token[len(_TOKEN_PREFIX):]
    try:
        raw = base64.b64decode(
            encoded + ("=" * (-len(encoded) % 4)),
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(raw.decode("utf-8"))
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise AttentionTokenError("invalid attention position") from exc
    if not isinstance(value, dict) or value.get("v") != 1:
        raise AttentionTokenError("unsupported attention position version")
    return value


def _position_token(
    *,
    logical_delegate_kind: str,
    logical_delegate_id: str,
    observed_session_id: str,
    continuity: str,
    event_id: int,
) -> str:
    return _encode_token(
        {
            "kind": "position",
            "logical_delegate_kind": logical_delegate_kind,
            "logical_delegate_id": logical_delegate_id,
            "observed_session_id": observed_session_id,
            "lineage_segment": observed_session_id,
            "continuity": continuity,
            "event_id": event_id,
        }
    )


def attention_position_session_id(position: str) -> str:
    """Return the exact lineage segment named by an attention position."""
    value = _decode_token(position)
    if value.get("kind") != "position":
        raise AttentionTokenError("attention token is not a position")
    session_id = value.get("observed_session_id")
    if not isinstance(session_id, str) or not session_id:
        raise AttentionTokenError("invalid attention position session identity")
    return session_id


def _reference_token(
    *,
    reason: AttentionReason,
    session_id: str,
    continuity: str,
    event_id: int,
    correlation_id: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "kind": "reference",
        "reason": reason.value,
        "session_id": session_id,
        "continuity": continuity,
        "event_id": event_id,
    }
    if correlation_id:
        payload["correlation_id"] = correlation_id
    return _encode_token(payload)


def _selected_reasons(reasons: Iterable[AttentionReason | str]) -> set[AttentionReason]:
    selected: set[AttentionReason] = set()
    for reason in reasons:
        try:
            selected.add(
                reason if isinstance(reason, AttentionReason) else AttentionReason(reason)
            )
        except ValueError as exc:
            raise AttentionTokenError(f"unsupported attention reason: {reason}") from exc
    if not selected:
        raise AttentionTokenError("at least one attention reason is required")
    return selected


def _position_event_id(
    position: str | None,
    *,
    logical_delegate_kind: str,
    logical_delegate_id: str,
    session_id: str,
    continuity: str | None,
) -> int:
    if position is None:
        return 0
    value = _decode_token(position)
    if value.get("kind") != "position":
        raise AttentionTokenError("attention token is not a position")
    expected = {
        "logical_delegate_kind": logical_delegate_kind,
        "logical_delegate_id": logical_delegate_id,
        "observed_session_id": session_id,
        "lineage_segment": session_id,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise AttentionTokenError("attention position targets a different delegate")
    if not continuity or value.get("continuity") != continuity:
        raise AttentionHistoryChangedError(
            "attention position history was replaced; restart from a fresh position"
        )
    event_id = value.get("event_id")
    if not isinstance(event_id, int) or event_id < 0:
        raise AttentionTokenError("invalid attention position event boundary")
    return event_id


def _request_availability(
    rows: list[dict[str, Any]],
    *,
    request_event_id: int,
    correlation_key: str,
    correlation_id: str,
    resolved_event: str,
    withdrawn_event: str | None = None,
    live: bool,
) -> str:
    for row in rows:
        if int(row["event_id"]) <= request_event_id:
            continue
        if row["event_type"] not in {resolved_event, withdrawn_event}:
            continue
        if str(row["data"].get(correlation_key) or "") != correlation_id:
            continue
        if withdrawn_event and row["event_type"] == withdrawn_event:
            return "withdrawn"
        return "resolved"
    return "available" if live else "unknown_after_restart"


def _event_reason(row: dict[str, Any]) -> AttentionReason | None:
    event_type = row["event_type"]
    data = row["data"]
    if event_type == "turn_complete":
        stop_reason = str(data.get("stop_reason") or "").lower()
        if stop_reason in _CANCELLED_STOP_REASONS:
            return AttentionReason.TURN_CANCELLED
        if stop_reason.startswith("interrupted"):
            return None
        return AttentionReason.TURN_COMPLETE
    if event_type == "ask_user_request":
        return AttentionReason.INPUT_REQUIRED
    if event_type == "permission_request" and data.get("request_id"):
        return AttentionReason.PERMISSION_REQUIRED
    if event_type == "policy_required" and data.get("action_id"):
        return AttentionReason.POLICY_REQUIRED
    if event_type == "terminal_unreachable":
        return AttentionReason.UNREACHABLE
    if event_type == "session_state_changed":
        return {
            "failed": AttentionReason.FAILED,
            "stopped": AttentionReason.STOPPED,
            "ended": AttentionReason.ENDED,
        }.get(str(data.get("status") or "").lower())
    return None


def _event_reference(
    session: Session,
    row: dict[str, Any],
    reason: AttentionReason,
    *,
    continuity: str,
    all_rows: list[dict[str, Any]],
) -> AttentionReference:
    data = row["data"]
    event_id = int(row["event_id"])
    correlation_id: str | None = None
    availability = "available"
    value: dict[str, Any] | None = None
    kind = {
        AttentionReason.TURN_COMPLETE: "result",
        AttentionReason.TURN_CANCELLED: "result",
        AttentionReason.INPUT_REQUIRED: "input",
        AttentionReason.PERMISSION_REQUIRED: "permission",
        AttentionReason.POLICY_REQUIRED: "policy",
    }.get(reason, "terminal")

    if reason == AttentionReason.INPUT_REQUIRED:
        correlation_id = str(data.get("tool_call_id") or "")
        value = {"tool_call_id": correlation_id}
        client = session.client
        live = bool(
            correlation_id
            and client
            and getattr(client, "has_pending_elicitation", lambda _value: False)(
                correlation_id
            )
        )
        availability = _request_availability(
            all_rows,
            request_event_id=event_id,
            correlation_key="tool_call_id",
            correlation_id=correlation_id,
            resolved_event="ask_user_resolved",
            withdrawn_event="ask_user_withdrawn",
            live=live,
        )
    elif reason == AttentionReason.PERMISSION_REQUIRED:
        correlation_id = str(data.get("request_id") or "")
        options = []
        for option in data.get("options") or []:
            if not isinstance(option, dict):
                continue
            option_id = str(option.get("optionId") or "")[:160]
            if not option_id:
                continue
            options.append(
                {
                    "option_id": option_id,
                    "name": str(option.get("name") or "")[:200] or None,
                    "kind": str(option.get("kind") or "")[:80] or None,
                }
            )
            if len(options) >= 20:
                break
        value = {
            "request_id": correlation_id,
            "options": options,
        }
        client = session.client
        live_reader = getattr(client, "has_pending_permission", None) if client else None
        live = bool(callable(live_reader) and live_reader(correlation_id))
        availability = _request_availability(
            all_rows,
            request_event_id=event_id,
            correlation_key="request_id",
            correlation_id=correlation_id,
            resolved_event="permission_resolved",
            live=live,
        )

    return AttentionReference(
        kind=kind,
        ref=_reference_token(
            reason=reason,
            session_id=session.session_id,
            continuity=continuity,
            event_id=event_id,
            correlation_id=correlation_id,
        ),
        availability=availability,
        value=value,
    )


def evaluate_owned_attention(
    *,
    db: Database,
    session: Session,
    requested_ref: str,
    reasons: Iterable[AttentionReason | str],
    position: str | None = None,
) -> AttentionWaitResponse:
    """Return the earliest selected durable boundary after ``position``."""
    if session.event_log is None:
        raise AttentionTokenError("session history is not loaded")

    selected = _selected_reasons(reasons)
    worktree_id = session.target.worktree_id
    logical_kind = "worktree" if worktree_id else "session"
    logical_id = str(worktree_id or session.session_id)
    continuity, events = session.event_log.snapshot_history(durable=True)
    all_rows = [
        {
            "event_id": event.id,
            "event_type": event.event,
            "data": event.data,
            "timestamp": event.timestamp,
        }
        for event in events
    ]
    start_event_id = _position_event_id(
        position,
        logical_delegate_kind=logical_kind,
        logical_delegate_id=logical_id,
        session_id=session.session_id,
        continuity=continuity,
    )
    successor_id: str | None = None
    current_session_id = session.session_id
    limitations: list[str] = []

    if AttentionReason.PERMISSION_REQUIRED in selected:
        limitations.append(
            "permission_required needs a correlated permission_request; "
            "uncorrelated legacy events do not settle"
        )
    if AttentionReason.UNREACHABLE in selected:
        limitations.append(
            "unreachable settles only from authoritative terminal_unreachable evidence"
        )
    if AttentionReason.POLICY_REQUIRED in selected:
        limitations.append(
            "policy_required settles only from a dedicated correlated policy event"
        )
    if AttentionReason.CONTRACT_CHANGED in selected:
        limitations.append(
            "contract_changed is client-synthesized while following a successor"
        )
    if AttentionReason.ENDED in selected:
        limitations.append(
            "ended is unavailable until deliberate retirement retains a durable "
            "terminal history"
        )

    observed_event_id = start_event_id
    boundary: dict[str, Any] | None = None
    boundary_reason: AttentionReason | None = None
    prior_handoff = next(
        (
            event
            for event in all_rows
            if event["event_type"] == "session_handoff"
            and int(event["event_id"]) <= start_event_id
        ),
        None,
    )
    if prior_handoff is not None:
        successor_id = str(
            prior_handoff["data"].get("rolled_to") or ""
        ) or None
        current_session_id = successor_id or current_session_id
        limitations.append(
            "successor compatibility must be probed before attention evaluation continues"
        )
    for event in all_rows:
        if prior_handoff is not None:
            break
        event_id = int(event["event_id"])
        if event_id <= start_event_id:
            continue
        observed_event_id = event_id
        if event["event_type"] == "session_handoff":
            successor_id = str(event["data"].get("rolled_to") or successor_id or "") or None
            current_session_id = successor_id or current_session_id
            limitations.append(
                "successor compatibility must be probed before attention evaluation continues"
            )
            break
        reason = _event_reason(event)
        if reason in selected:
            boundary = event
            boundary_reason = reason
            break

    identity = AttentionIdentity(
        logical_delegate_kind=logical_kind,
        logical_delegate_id=logical_id,
        requested_ref=requested_ref,
        observed_session_id=session.session_id,
        current_session_id=current_session_id,
        successor_id=successor_id,
    )
    next_position = (
        _position_token(
            logical_delegate_kind=logical_kind,
            logical_delegate_id=logical_id,
            observed_session_id=session.session_id,
            continuity=continuity,
            event_id=(
                int(boundary["event_id"]) if boundary is not None else observed_event_id
            ),
        )
        if continuity
        else None
    )
    if boundary is None or boundary_reason is None:
        return AttentionWaitResponse(
            settled=False,
            identity=identity,
            position=next_position,
            limitations=limitations,
        )
    return AttentionWaitResponse(
        settled=True,
        reason=boundary_reason,
        identity=identity,
        position=next_position,
        boundary_event_id=int(boundary["event_id"]),
        reference=_event_reference(
            session,
            boundary,
            boundary_reason,
            continuity=continuity,
            all_rows=all_rows,
        ),
        limitations=limitations,
    )
