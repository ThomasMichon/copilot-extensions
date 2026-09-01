"""Bounded, cursor-neutral delegated-result projections."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .models import (
    DelegatedResultSnapshot,
    ResultCurrentState,
    ResultFidelity,
    ResultField,
    ResultIdentity,
    ResultIncrement,
    ResultLimits,
    ResultTruncation,
    ResultWorkItem,
    SessionStatus,
)

if TYPE_CHECKING:
    from .db import Database
    from .events import EventLog
    from .session_manager import Session


DEFAULT_MAX_ITEMS = 20
MAX_MAX_ITEMS = 100
DEFAULT_MAX_TEXT_CHARS = 6000
MAX_MAX_TEXT_CHARS = 20000
_MAX_SCAN_EVENTS = 512
_MAX_DETAIL_TOKEN_CHARS = 2048
_TOKEN_PREFIX = "abr1."
_TERMINAL_TOOL_STATUSES = frozenset(
    {
        "completed",
        "complete",
        "success",
        "succeeded",
        "failed",
        "error",
        "cancelled",
        "canceled",
    }
)


class ResultTokenError(ValueError):
    """An opaque result position or detail reference is malformed."""


@dataclass
class _TextBudget:
    limit: int
    used: int = 0

    def clip(self, value: Any, *, field_limit: int | None = None) -> tuple[str, bool, int]:
        text = "" if value is None else str(value)
        original = len(text)
        allowed = max(0, self.limit - self.used)
        if field_limit is not None:
            allowed = min(allowed, max(0, field_limit))
        if original <= allowed:
            self.used += original
            return text, False, original
        if allowed <= 0:
            return "", original > 0, original
        if allowed <= 3:
            clipped = "." * allowed
        else:
            clipped = text[: allowed - 3] + "..."
        self.used += len(clipped)
        return clipped, True, original


def normalize_bounds(max_items: int, max_text_chars: int) -> tuple[int, int]:
    """Clamp caller-supplied bounds to the public contract."""
    return (
        max(1, min(int(max_items), MAX_MAX_ITEMS)),
        max(256, min(int(max_text_chars), MAX_MAX_TEXT_CHARS)),
    )


def _encode_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"v": 1, **payload}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return _TOKEN_PREFIX + encoded


def _decode_token(
    token: str,
    *,
    source: str,
    session_id: str,
    kinds: frozenset[str],
) -> dict[str, Any]:
    if not token or len(token) > _MAX_DETAIL_TOKEN_CHARS or not token.startswith(
        _TOKEN_PREFIX
    ):
        raise ResultTokenError("invalid result token")
    encoded = token[len(_TOKEN_PREFIX):]
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.b64decode(
            encoded + padding, altchars=b"-_", validate=True
        )
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResultTokenError("invalid result token") from exc
    if not isinstance(value, dict) or value.get("v") != 1:
        raise ResultTokenError("unsupported result token version")
    if value.get("source") != source or value.get("session_id") != session_id:
        raise ResultTokenError("result token targets a different session")
    if value.get("kind") not in kinds:
        raise ResultTokenError("result token has the wrong kind")
    return value


def _position_token(
    source: str, session_id: str, continuity: str, event_id: int
) -> str:
    return _encode_token(
        {
            "kind": "position",
            "source": source,
            "session_id": session_id,
            "continuity": continuity,
            "event_id": event_id,
        }
    )


def _event_ref(
    source: str, session_id: str, continuity: str, event_id: int
) -> str:
    return _encode_token(
        {
            "kind": "event",
            "source": source,
            "session_id": session_id,
            "continuity": continuity,
            "event_id": event_id,
        }
    )


def _turn_ref(session_id: str, turn_index: int) -> str:
    return _encode_token(
        {
            "kind": "turn",
            "source": "owned",
            "session_id": session_id,
            "turn_index": turn_index,
        }
    )


def _iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _bounded_progress(
    progress: dict[str, Any], budget: _TextBudget
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in sorted(progress)[:16]:
        bounded_key, _, _ = budget.clip(key, field_limit=40)
        bounded_value, _, _ = budget.clip(progress[key], field_limit=160)
        if bounded_key:
            result[bounded_key] = bounded_value
        if budget.used >= budget.limit:
            break
    return result


def _active_work(session: Session, budget: _TextBudget) -> ResultField:
    value: dict[str, Any] = {}
    active = session.event_log.active_tool_call() if session.event_log else None
    if active:
        tool_call_id, tool_call_id_truncated, _ = budget.clip(
            active.get("tool_call_id"), field_limit=160
        )
        title, _, _ = budget.clip(active.get("title"), field_limit=200)
        kind, kind_truncated, _ = budget.clip(
            active.get("kind"), field_limit=80
        )
        command, command_truncated, _ = budget.clip(
            active.get("command"), field_limit=500
        )
        value["tool"] = {
            "tool_call_id": tool_call_id or None,
            "tool_call_id_truncated": tool_call_id_truncated,
            "title": title or "tool",
            "kind": kind or None,
            "kind_truncated": kind_truncated,
            "command": command or None,
            "command_truncated": command_truncated,
            "started_at": _iso_timestamp(active.get("started_at")),
            "elapsed_s": (
                max(0.0, time.time() - float(active["started_at"]))
                if isinstance(active.get("started_at"), (int, float))
                else None
            ),
        }
    milestones = _bounded_progress(dict(session.progress), budget)
    if milestones:
        value["milestones"] = milestones
    return ResultField(availability="available", value=value or None)


def _pending_input(session: Session, budget: _TextBudget) -> ResultField:
    client = session.client
    pending_reader = getattr(client, "pending_ask_user", None) if client else None
    if callable(pending_reader):
        pending: list[dict[str, Any]] = []
        for item in pending_reader()[:3]:
            message, truncated, _ = budget.clip(
                item.get("message"), field_limit=240
            )
            schema = item.get("requested_schema")
            properties = (
                list(schema.get("properties", {}).keys())[:20]
                if isinstance(schema, dict)
                and isinstance(schema.get("properties"), dict)
                else []
            )
            fields: list[str] = []
            for name in properties:
                field_name, _, _ = budget.clip(name, field_limit=80)
                if field_name:
                    fields.append(field_name)
                if budget.used >= budget.limit:
                    break
            tool_call_id, tool_call_id_truncated, _ = budget.clip(
                item.get("tool_call_id"), field_limit=160
            )
            pending.append(
                {
                    "tool_call_id": tool_call_id or None,
                    "tool_call_id_truncated": tool_call_id_truncated,
                    "message": message or None,
                    "message_truncated": truncated,
                    "fields": fields,
                }
            )
        return ResultField(availability="available", value=pending)
    if session.turn_count <= 0:
        return ResultField(
            availability="not_yet_observed",
            reason="the session has not started a turn",
        )
    return ResultField(
        availability="unknown_after_restart",
        reason="pending input is live client state and was not recoverable",
    )


def _latest_result(
    db: Database, session_id: str, budget: _TextBudget
) -> tuple[ResultField, str | None]:
    row = db.get_latest_completed_turn(session_id)
    if row is None:
        return (
            ResultField(
                availability="not_yet_observed",
                reason="no completed turn is available",
            ),
            None,
        )

    stop_reason = row.get("stop_reason")
    response_text = row.get("response_text") or ""
    text, truncated, original = budget.clip(response_text, field_limit=4000)
    bounded_stop_reason, stop_reason_truncated, _ = budget.clip(
        stop_reason, field_limit=300
    )
    incomplete = bool(
        stop_reason
        and str(stop_reason).lower().startswith(
            (
                "interrupted",
                "error:",
                "cancel",
                "max_tokens",
                "max_turn_requests",
            )
        )
    )
    availability = "partial" if incomplete else "available"
    reason = (
        "the settled turn has no complete assistant result"
        if availability == "partial"
        else None
    )
    value = {
        "turn_index": row["turn_index"],
        "text": text or None,
        "stop_reason": bounded_stop_reason or None,
        "stop_reason_truncated": stop_reason_truncated,
        "started_at": _iso_timestamp(row.get("started_at")),
        "completed_at": _iso_timestamp(row.get("completed_at")),
    }
    return (
        ResultField(
            availability=availability,
            value=value,
            reason=reason,
            detail_ref=_turn_ref(session_id, int(row["turn_index"])),
            truncation=ResultTruncation(
                truncated=truncated,
                original_chars=original,
                emitted_chars=len(text),
            ),
        ),
        str(stop_reason) if stop_reason is not None else None,
    )


def _attention(
    session: Session, pending_input: ResultField, latest_stop_reason: str | None
) -> ResultField:
    if (
        pending_input.availability == "available"
        and isinstance(pending_input.value, list)
        and pending_input.value
    ):
        return ResultField(availability="available", value="input_required")
    if session.status == SessionStatus.FAILED:
        return ResultField(availability="available", value="failed")
    if session.status == SessionStatus.STOPPED:
        return ResultField(availability="available", value="stopped")
    if session.status == SessionStatus.ENDED:
        return ResultField(availability="available", value="ended")
    if latest_stop_reason:
        lowered = latest_stop_reason.lower()
        if lowered.startswith("error:"):
            return ResultField(availability="available", value="failed")
        if lowered.startswith(("interrupted", "cancel")):
            return ResultField(
                availability="unknown_after_restart",
                reason="the durable turn row does not distinguish explicit cancellation",
            )
    if session.status == SessionStatus.IDLE and session.turn_count > 0:
        return ResultField(availability="available", value="turn_complete")
    return ResultField(availability="available", value=None)


def _event_parts(
    event_type: str, data: dict[str, Any]
) -> tuple[str, Any, str | None] | None:
    if event_type == "agent_message":
        return ("assistant_message", data.get("text"), None)
    if event_type == "tool_call_start":
        return (
            "tool_started",
            data.get("title") or data.get("kind") or "tool",
            "running",
        )
    if event_type == "tool_call_update":
        status = str(data.get("status") or "")
        if status.lower() not in _TERMINAL_TOOL_STATUSES:
            return None
        return (
            "tool_finished",
            data.get("title") or data.get("kind") or "tool",
            status or None,
        )
    if event_type == "plan_update":
        return ("plan", data.get("title"), None)
    if event_type == "ask_user_request":
        return ("input_required", data.get("message"), "pending")
    if event_type == "permission_request":
        summary = data.get("intention") or data.get("kind") or "permission required"
        return ("permission_required", summary, "pending")
    if event_type == "turn_complete":
        return ("turn_complete", data.get("stop_reason"), data.get("stop_reason"))
    if event_type == "error":
        return ("error", data.get("message"), "failed")
    if event_type == "session_handoff":
        return (
            "session_handoff",
            data.get("rolled_to") or data.get("reason"),
            "continued",
        )
    if event_type == "prompt_enqueued":
        return ("prompt_enqueued", f"position {data.get('position')}", "queued")
    if event_type == "prompt_dequeued":
        return ("prompt_dequeued", f"queue {data.get('queue_id')}", "started")
    return None


def _incremental_owned(
    db: Database,
    event_log: EventLog,
    session_id: str,
    *,
    position: str | None,
    max_items: int,
    budget: _TextBudget,
) -> ResultIncrement:
    after_id: int | None = None
    decoded: dict[str, Any] | None = None
    if position:
        decoded = _decode_token(
            position,
            source="owned",
            session_id=session_id,
            kinds=frozenset({"position"}),
        )
        try:
            after_id = int(decoded["event_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResultTokenError("invalid result position") from exc

    scan_limit = min(_MAX_SCAN_EVENTS, max(64, max_items * 8))
    truncated_before = False
    raw_more = False
    continuity, head_id, rows = event_log.snapshot_window(
        after=after_id, limit=scan_limit + 1, durable=True
    )
    if not continuity or head_id <= 0:
        return ResultIncrement(
            availability="not_yet_observed",
            reason="the event log has no durable origin yet",
        )
    if decoded is not None and decoded.get("continuity") != continuity:
        return ResultIncrement(
            availability="discontinuous",
            position=_position_token("owned", session_id, continuity, head_id),
            reason="the event log was rebuilt or replaced",
        )
    if after_id is not None and (after_id < 0 or after_id > head_id):
        raise ResultTokenError("result position is outside the current event log")
    if after_id is None:
        if len(rows) > scan_limit:
            rows = rows[1:]
            truncated_before = True
    else:
        if len(rows) > scan_limit:
            rows = rows[:scan_limit]
            raw_more = True

    items: list[ResultWorkItem] = []
    last_processed = after_id or 0
    stopped_early = False
    iter_rows = list(reversed(rows)) if after_id is None else rows
    for event in iter_rows:
        event_id = event.id
        event_type = event.event
        data = event.data
        timestamp = event.timestamp
        parts = _event_parts(event_type, data)
        if parts is None:
            last_processed = event_id
            continue
        kind, raw_summary, status = parts
        summary, clipped, original = budget.clip(raw_summary, field_limit=1000)
        if original and not summary:
            stopped_early = True
            break
        bounded_status, status_clipped, _ = budget.clip(status, field_limit=80)
        items.append(
            ResultWorkItem(
                event_id=event_id,
                kind=kind,
                summary=summary or None,
                status=bounded_status or None,
                timestamp=timestamp,
                detail_ref=_event_ref(
                    "owned", session_id, continuity, event_id
                ),
                truncated=clipped or status_clipped,
            )
        )
        last_processed = event_id
        if len(items) >= max_items:
            stopped_early = True
            break

    if after_id is None:
        items.reverse()
        truncated_before = truncated_before or stopped_early
    next_event_id = head_id if after_id is None else last_processed
    return ResultIncrement(
        availability="available",
        items=items,
        position=_position_token(
            "owned", session_id, continuity, next_event_id
        ),
        has_more=bool(after_id is not None and (raw_more or stopped_early)),
        truncated_before=truncated_before,
    )


def build_owned_result_snapshot(
    *,
    db: Database,
    session: Session,
    requested_ref: str,
    position: str | None,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
) -> DelegatedResultSnapshot:
    """Build a bounded result snapshot for a bridge-owned session."""
    max_items, max_text_chars = normalize_bounds(max_items, max_text_chars)
    state_budget = _TextBudget(max(64, max_text_chars // 4))
    latest_budget = _TextBudget(max(128, max_text_chars // 2))
    incremental_budget = _TextBudget(
        max_text_chars - state_budget.limit - latest_budget.limit
    )
    row = db.get_session(session.session_id) or {}
    successor_id = row.get("successor_id")
    predecessor_id = row.get("predecessor_id")
    current_session_id = str(successor_id or session.session_id)
    worktree_id = session.target.worktree_id

    active_work = _active_work(session, state_budget)
    pending_input = _pending_input(session, state_budget)
    latest_result, latest_stop_reason = _latest_result(
        db, session.session_id, latest_budget
    )
    incremental = _incremental_owned(
        db,
        session.event_log,
        session.session_id,
        position=position,
        max_items=max_items,
        budget=incremental_budget,
    )
    attention = _attention(session, pending_input, latest_stop_reason)

    return DelegatedResultSnapshot(
        identity=ResultIdentity(
            logical_delegate_kind="worktree" if worktree_id else "session",
            logical_delegate_id=worktree_id or session.session_id,
            requested_ref=requested_ref,
            snapshot_session_id=session.session_id,
            current_session_id=current_session_id,
            predecessor_id=predecessor_id,
            successor_id=successor_id,
        ),
        fidelity=ResultFidelity(
            level="full",
            event_retention="durable",
        ),
        state=ResultCurrentState(
            session_status=session.status,
            liveness=session.liveness_state(),
            context_pct=session.context_pct,
            usage_model=session.usage_model,
            attention=attention,
            active_work=active_work,
            pending_input=pending_input,
        ),
        latest_result=latest_result,
        incremental=incremental,
        limits=ResultLimits(
            max_items=max_items,
            max_text_chars=max_text_chars,
            used_text_chars=(
                state_budget.used + latest_budget.used + incremental_budget.used
            ),
        ),
    )


def expand_owned_result_ref(
    *,
    db: Database,
    event_log: EventLog,
    session_id: str,
    token: str,
) -> dict[str, Any]:
    """Resolve one opaque owned-session event or turn detail reference."""
    decoded = _decode_token(
        token,
        source="owned",
        session_id=session_id,
        kinds=frozenset({"event", "turn"}),
    )
    if decoded["kind"] == "event":
        try:
            event_id = int(decoded["event_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResultTokenError("invalid event detail reference") from exc
        continuity, event = event_log.snapshot_event(event_id, durable=True)
        if not continuity or decoded.get("continuity") != continuity:
            raise ResultTokenError("result detail belongs to replaced history")
        if event is None:
            raise KeyError("event detail is no longer available")
        return {
            "kind": "event",
            "session_id": session_id,
            "event": {
                "id": event.id,
                "event": event.event,
                "data": event.data,
                "timestamp": event.timestamp,
            },
        }

    try:
        turn_index = int(decoded["turn_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ResultTokenError("invalid turn detail reference") from exc
    row = db.get_turn(session_id, turn_index)
    if row is None:
        raise KeyError("turn detail is no longer available")
    try:
        tool_calls = json.loads(row.get("tool_calls_json") or "[]")
    except (ValueError, TypeError):
        tool_calls = []
    return {
        "kind": "turn",
        "session_id": session_id,
        "turn": {
            "turn_index": row["turn_index"],
            "prompt": row.get("prompt") or "",
            "response_text": row.get("response_text") or "",
            "thought_text": row.get("thought_text") or "",
            "stop_reason": row.get("stop_reason"),
            "tool_calls": tool_calls if isinstance(tool_calls, list) else [],
            "started_at": _iso_timestamp(row.get("started_at")),
            "completed_at": _iso_timestamp(row.get("completed_at")),
        },
    }
