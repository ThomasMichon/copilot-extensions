"""Pull-only, bounded observations; neither recovery nor a progress authority."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from itertools import islice
from typing import Any

from agent_remote_driver.session_host.client import SessionHostClient

from .attention_current import _REQUESTS, _RESOLUTIONS, _TURN_ENDS
from .attention_wait import _input_correlation
from .events import is_nested_event_data
from .result_snapshot import _event_parts
from .result_tokens import _event_ref

WINDOW = 64
FRESH_SECONDS = 60.0


def _age(value: Any, now: float) -> float | None:
    try:
        if type(value) not in (int, float) or not math.isfinite(value) or value > now:
            return None
        return max(0.0, now - float(value))
    except OverflowError:
        return None


def _iso(value: Any) -> str | None:
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _text(value: Any, limit: int = 160) -> str | None:
    return value[:limit] if type(value) is str else None


def _semantic(
    session: Any, status: str, now: float, host: dict[str, Any],
) -> dict[str, Any]:
    log = session.event_log
    continuity, floor, head, events = (
        log.snapshot_observation(limit=WINDOW) if log is not None else (None, 0, 0, [])
    )
    malformed = any(type(event.data) is not dict for event in events)
    root = [
        event for event in events
        if type(event.data) is dict and not is_nested_event_data(event.data)
        and event.data.get("history_replay") is not True
    ]
    latest = root[-1] if root else None
    age = _age(latest.timestamp, now) if latest is not None else None
    freshness = "unknown" if age is None else ("fresh" if age <= FRESH_SECONDS else "stale")
    truncated = bool(events and (floor > 1 or events[0].id > floor))
    contiguous = all(right.id == left.id + 1 for left, right in zip(events, events[1:]))
    evidence = {
        "availability": "unavailable", "reference": None,
        "reason": "no-retained-evidence",
    }
    referable = [
        event for event in root if _event_parts(event.event, event.data) is not None
    ]
    if referable and continuity and contiguous:
        evidence = {
            "availability": "available",
            "reference": _event_ref(
                "owned", session.session_id, continuity, referable[-1].id,
            ),
            "reason": None,
            "scope": "latest-resolvable-retained-event-not-entire-projection",
        }
    elif events:
        evidence["reason"] = (
            "redacted-or-unsupported-evidence" if continuity and contiguous
            else "history-discontinuous"
        )

    active = log.active_tool_call(include_nested=False) if log is not None else None
    tool = None
    if active:
        updates = [
            event for event in root
            if event.event in {"tool_call_start", "tool_call_update"}
            and event.data.get("tool_call_id") == active.get("tool_call_id")
        ]
        tool = {
            "title": _text(active.get("title")),
            "elapsed_s": _age(active.get("started_at"), now),
            "update_age_s": _age(updates[-1].timestamp, now) if updates else None,
            "update_evidence": "retained-event" if updates else "outside-retained-window",
        }
    thoughts = [event for event in root if event.event == "agent_thought"]
    declarations = list(islice(session.progress.items(), 17))
    declared = [
        {"name": _text(key, 40), "value": _text(value), "basis": "declared"}
        for key, value in declarations[:16]
        if type(key) is str and type(value) is str
    ]
    truncated = truncated or len(declarations) > 16 or any(
        type(key) is not str or type(value) is not str or len(key) > 40 or len(value) > 160
        for key, value in declarations
    )
    if active and type(active.get("title")) is str and len(active["title"]) > 160:
        truncated = True
    request_state = {
        "created": "requested", "starting": "requested", "running": "running",
    }.get(status, "unknown")
    terminal = "not-terminal" if request_state in {"requested", "running"} else "unknown"
    reason: str | None = "no-semantic-result"
    if latest is None:
        request_state, terminal = "unknown", "unknown"
    pending: dict[tuple[str, str, str], Any] = {}
    unkeyed: list[Any] = []
    last_end = None
    for event in root:
        if event.event in _TURN_ENDS:
            pending.clear()
            unkeyed.clear()
            last_end = event if event.event == "turn_complete" else None
        elif event.event in _REQUESTS:
            family, key, _reason = _REQUESTS[event.event]
            key, correlation = (
                _input_correlation(event.data) if family == "input"
                else (key, str(event.data.get(key) or ""))
            )
            if correlation:
                pending[(family, key, correlation)] = event
            else:
                unkeyed.append(event)
            last_end = None
        elif event.event in _RESOLUTIONS:
            family, key = _RESOLUTIONS[event.event]
            key, correlation = (
                _input_correlation(event.data) if family == "input"
                else (key, str(event.data.get(key) or ""))
            )
            pending.pop((family, key, correlation), None)
        elif event.event in {"user_message", "tool_call_start", "agent_message", "agent_thought"} or (
            event.event == "session_state_changed" and event.data.get("status") == "running"
        ):
            last_end = None
    has_pending = bool(pending or unkeyed)
    if has_pending and status in {"running", "idle"}:
        request_state, terminal = "blocked", "not-terminal"
    if last_end is not None and status == "idle":
        stop = last_end.data.get("stop_reason")
        if stop == "end_turn":
            terminal, request_state, reason = "success", "complete", None
        elif type(stop) is str and stop.lower().startswith(("error", "fail")):
            terminal, request_state, reason = "failure", "complete", None
        else:
            terminal, reason = "unknown", "terminal-outcome-not-reported"
    snapshot = host.get("snapshot") or {}
    contradictory = (
        (snapshot.get("child_alive") is False and status == "running")
        or (snapshot.get("turn_in_flight") is True and request_state == "complete")
    )
    if contradictory or malformed or not contiguous:
        request_state, terminal = "unknown", "unknown"
        reason = "contradictory-sources" if contradictory else "malformed-or-partial-projection"
    elif freshness != "fresh" and terminal != "success" and terminal != "failure":
        reason = "stale-semantic-signal" if freshness == "stale" else "no-semantic-signal"
    return {
        "source": "bridge-retained-root-events", "observed_at": _iso(now),
        "scope": "bounded-retained-window-not-task-completion",
        "proof": "observed-events-not-host-round-trip",
        "freshness": freshness, "signal_age_s": age,
        "truncation": "truncated" if truncated else ("none" if events else "unknown"),
        "request_state": request_state, "terminal_result": terminal, "unknown_result": reason,
        "current_phase": _text(session.progress.get("phase")) or (
            "tool" if active else ("awaiting-input" if has_pending else status)
        ),
        "active_tool": tool,
        "event_sequence_delta": (
            events[-1].id - events[0].id if events and contiguous else None
        ),
        "delta_scope": "first-to-last-event-in-retained-window",
        "last_thought_age_s": _age(thoughts[-1].timestamp, now) if thoughts else None,
        "thought_chunk_count": len(thoughts),
        "thought_scope": "retained-window-only-content-omitted",
        "redaction": "thought-content-omitted",
        "milestones": {
            "declared": declared,
            "derived": [{"name": "request-state", "value": request_state, "basis": "derived"}],
        },
        "evidence": evidence, "window_limit": WINDOW, "observed_head": head,
    }


async def inspect_session(session: Any) -> dict[str, Any]:
    client = getattr(session.client, "session_host_client", None) if session.client else None
    host = (
        await client.inspect() if isinstance(client, SessionHostClient)
        else {"state": "unsupported", "reason": "provider-has-no-read-only-inspection"}
    )
    now = time.time()
    status, _at_rest, liveness = session.public_state()
    state = status.value
    snapshot = host.get("snapshot") or {}
    responsive = host.get("state") == "responsive"
    semantic = _semantic(session, state, now, host)
    target = session.target
    forwarded = bool(target.type == "ssh" or target.codespace or target.container)
    return {
        "contract": "agent-bridge.session-inspection", "version": 1,
        "source": "existing-bridge-session", "observed_at": _iso(now),
        "proof": "read-only-observation-not-authority-or-completion",
        "layers": {
            "daemon_api": {"state": "responsive", "age_s": 0.0, "scope": "this-request"},
            "provider_forward": {
                "state": ("path-responsive" if responsive else "unknown") if forwarded else "not-applicable",
                "age_s": 0.0 if responsive and forwarded else None,
                "scope": "existing-route-only-no-forward-refresh",
            },
            "session_host": {
                **host, "age_s": 0.0 if responsive else None,
                "scope": "host-event-loop-round-trip-not-child-or-semantic-progress",
            },
            "copilot_child": {
                "state": (
                    "alive" if snapshot["child_alive"] else "exited"
                ) if responsive else "unknown",
                "age_s": 0.0 if responsive else None,
                "scope": "host-observed-process-only",
            },
            "acp_turn": {
                "state": (
                    "in-flight" if snapshot["turn_in_flight"] else "no-observed-turn"
                ) if responsive else "unknown",
                "age_s": snapshot.get("last_child_frame_age_s"),
                "scope": "host-observed-prompt-requests",
            },
            "semantic_progress": semantic,
        },
        "liveness": liveness,
        "last_output_at": _iso(session.last_output_at),
        "last_heartbeat_at": _iso(session.last_heartbeat_at),
    }
