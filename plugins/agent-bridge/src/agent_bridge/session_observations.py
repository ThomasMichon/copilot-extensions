"""Semantic projection over existing admitted logs, without actor activation."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import pairwise
from typing import Any

from .compaction_evidence import CompactionSource, from_start_event
from .events import EventLog, SseEvent
from .session_observation_contract import (
    HISTORY,
    OBSERVATIONS,
    VERSION,
    ObservationError,
    ReadRequest,
    encode,
    position,
    read_position,
    text,
)

_KINDS = {
    "ask_user_request": "input-request",
    "ask_user_resolved": "input-cleared",
    "ask_user_withdrawn": "input-cleared",
    "permission_request": "permission-request",
    "permission_resolved": "permission-cleared",
    "policy_required": "policy-request",
    "compaction_start": "compaction",
    "compaction_complete": "compaction",
    "context_pressure": "context-pressure",
    "subagent_started": "contribution-reported",
    "subagent_completed": "contribution-reported",
    "subagent_failed": "contribution-reported",
    "session_handoff": "reported-lifecycle",
    "turn_complete": "reported-lifecycle",
    "session_state_changed": "reported-lifecycle",
    "tool_call_start": "reported-lifecycle",
    "tool_call_update": "reported-lifecycle",
}
_DETAILS = (
    "tool_call_id",
    "request_id",
    "action_id",
    "agent_id",
    "parent_tool_call_id",
    "status",
    "state",
    "successor_id",
    "rolled_to",
    "title",
    "kind",
    "stop_reason",
)


def _timestamp(value: Any) -> str | None:
    if type(value) not in {int, float}:
        return None
    try:
        if math.isfinite(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


@dataclass(frozen=True)
class KnownSource:
    """An authenticated exact registration/log, not a relationship authority."""

    log: EventLog
    source: dict[str, Any]
    registry: str
    session: Any = None

    def identity(self, continuity: str | None) -> dict[str, Any]:
        return {**self.source, "journal_incarnation": continuity}


def admit(state: Any, request: ReadRequest) -> KnownSource:
    """Read only known exact entries. Never resolve a worktree/agent or start one."""
    selection = request.selection
    mgr = getattr(state, "session_manager", None)
    if mgr is None:
        raise ObservationError("source-unavailable", "session manager is not ready")
    generation = text(getattr(mgr, "_generation_id", None), "hosting generation")
    # get_session is an in-memory lookup, but also accepts ACP aliases: reject
    # that alias rather than making it a new observation admission.
    session = mgr.get_session(selection.session_id)
    if session is not None and session.session_id == selection.session_id:
        worktree = session.target.worktree_id
        project = session.target.project
        log = session.event_log
        registry = "owned"
        durable_session = session.acp_session_id
    else:
        row = state.db.get_live_session(selection.session_id)
        if row is None or row["session_id"] != selection.session_id:
            raise ObservationError("source-unavailable", "no exact admitted session source")
        # A retained row's absence/expiry is not session retirement or claim release.
        if row.get("status") != "live":
            raise ObservationError("source-unavailable", "represented registration is not live")
        worktree, project = row.get("worktree_id"), None
        from .live_representation import MergePendingError

        try:
            log, _merges = state.live_event_store.snapshot(selection.session_id, timeout=0)
        except MergePendingError as exc:
            raise ObservationError("source-unavailable", "represented history is merging") from exc
        registry = "represented"
        durable_session = selection.session_id
        session = None
    if worktree != selection.worktree_id or project != selection.project:
        raise ObservationError(
            "selection-mismatch", "stored project/worktree does not match the selection"
        )
    if selection.head_session_id is not None:
        # Neither the Bridge hosting record nor the represented registration is
        # canonical Worktrees head authority. Do not echo caller assertions as facts.
        raise ObservationError("head-unavailable", "this source cannot qualify a canonical head")
    if not isinstance(log, EventLog):
        raise ObservationError(
            "source-unavailable", "no retained event log in this hosting generation"
        )
    source = {
        "provider": "bridge",
        "project": project,
        "worktree_id": worktree,
        "head_session_id": None,
        "session_id": selection.session_id,
        "durable_session_id": durable_session,
        "hosting_generation": generation,
        "registry": registry,
        "transport": session.target.type if session is not None else "represented-sdk",
    }
    if durable_session is not None:
        text(durable_session, "stored durable session identity")
    encode(source)
    return KnownSource(log, source, registry, session)


def entry(event: SseEvent, source: dict[str, Any]) -> dict[str, Any]:
    """Retain native event identity and bounded diagnostics, without prose inference."""
    if type(event.id) is not int or event.id < 1:
        raise ObservationError("invalid-source", "invalid native event identity")
    native_kind = text(event.event, "native event kind")
    if not isinstance(event.data, dict):
        raise ObservationError("invalid-source", "native event data must be an object")
    details: dict[str, Any] = {}
    omitted: list[str] = []
    for key in _DETAILS:
        value = event.data.get(key)
        if isinstance(value, str) and len(value) <= 512:
            details[key] = value
        elif value is not None:
            omitted.append(key)
    if native_kind == "compaction_start":
        details["compaction_evidence"] = from_start_event(
            event,
            source=CompactionSource(
                kind="represented-sdk" if source["registry"] == "represented" else "owned-acp",
                bridge_session_id=source["session_id"],
                durable_session_id=source["durable_session_id"],
                continuity_id=source["journal_incarnation"],
            ),
        ).as_dict()
    payload_ref = position(source, event.id)
    result = {
        "id": payload_ref,
        "native_event_id": event.id,
        "native_kind": native_kind,
        "kind": _KINDS.get(native_kind, "source-event"),
        "evidence_kind": (
            "reported-lifecycle"
            if _KINDS.get(native_kind)
            in {
                "reported-lifecycle",
                "contribution-reported",
            }
            else "retained-event"
        ),
        "occurred_at": _timestamp(event.timestamp),
        "timestamp_basis": "bridge-event-recorded",
        "details": details,
        "omitted_fields": omitted,
        "payload_ref": payload_ref,
        "payload_omitted": True,
        "answerability": "unknown",
        "authority": "reported",
    }
    result["content_sha256"] = hashlib.sha256(encode(result)).hexdigest()
    return result


def frame(
    known: KnownSource,
    request: ReadRequest,
    *,
    after: str | None = None,
) -> dict[str, Any]:
    """Build snapshot/delta/page from one atomic bounded retained-log read."""
    token = after if after is not None else request.position
    # Decode against the current source, then recheck the atomic snapshot's
    # incarnation below. Rebuild during either step is a gap, never a mixed page.
    identity = known.identity(known.log.continuity_id)
    boundary = read_position(token, identity) if token is not None else None
    continuity, floor, head, events = known.log.snapshot_observation(
        after=boundary if not request.history else None,
        before=boundary if request.history else None,
        limit=request.limit,
    )
    source = known.identity(continuity)
    if source != identity:
        raise ObservationError("source-changed", "event history changed during the read")
    if boundary is not None and (
        boundary > head + (1 if request.history else 0)
        or (floor and boundary < floor - (0 if request.history else 1))
    ):
        raise ObservationError("retention-gap", "position lies outside the retained event range")
    ids = [event.id for event in events]
    if (
        ids != sorted(set(ids))
        or any(right != left + 1 for left, right in pairwise(ids))
        or (events and boundary is not None and not request.history and ids[0] != boundary + 1)
    ):
        raise ObservationError("retention-gap", "retained events have a native identity gap")
    is_delta = not request.history and boundary is not None
    body: dict[str, Any] = {
        "schema": HISTORY if request.history else OBSERVATIONS,
        "version": VERSION,
        "kind": "history-page" if request.history else ("delta" if is_delta else "snapshot"),
        "source": source,
        "availability": "observed" if continuity else "unknown",
        "coverage": "partial",
        "durability": "unknown",
        "retained": {"floor": floor, "head": head, "complete": False},
        "position": None,
        "base_position": token if is_delta else None,
        "before": token if request.history else None,
        "next_before": None,
        "has_more": False,
        "entries": [],
        "current_requests": "unknown",
        "limitations": [
            "retained-events-are-not-canonical-custody",
            "visibility-is-not-a-durable-barrier",
            "canonical-head-unknown",
            "represented-history-memory-only"
            if known.registry == "represented"
            else "hosting-log-not-lifetime-history",
        ],
    }
    candidates = events if is_delta else list(reversed(events))
    accepted: list[dict[str, Any]] = []
    for event in candidates:
        item = entry(event, source)
        proposed = [*accepted, item] if is_delta else [item, *accepted]
        body["entries"] = proposed
        body["position"] = position(source, proposed[-1]["native_event_id"])
        body["next_before"] = position(source, proposed[0]["native_event_id"])
        try:
            encode(body)
        except ObservationError as exc:
            if exc.code != "body-too-large" or not accepted:
                raise
            break
        accepted = proposed
    body["entries"] = accepted
    end = (
        accepted[-1]["native_event_id"]
        if accepted
        else (boundary if is_delta and boundary is not None else head)
    )
    start = accepted[0]["native_event_id"] if accepted else (boundary or floor)
    body["position"] = position(source, end) if continuity else None
    body["next_before"] = position(source, start) if accepted and start > floor else None
    body["has_more"] = end < head if is_delta else bool(accepted and start > floor)
    encode(body)
    return body
