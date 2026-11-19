"""Metadata-only adapters at authoritative event boundaries; no transcript reads."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .events import is_nested_event_data
from .status_store import StatusKind, StatusStore

if TYPE_CHECKING:
    from .db import Database
    from .live_questions import QuestionObservation

_EVENTS: dict[str, StatusKind] = {
    "ask_user_request": "question_pending",
    "permission_request": "permission_pending",
    "turn_complete": "idle",
    "error": "error",
}


def owned_event_identity(session_id: str, epoch: str, event_id: int) -> str:
    return f"owned:{session_id}:{epoch}:{event_id}"


def source_key(session_id: str, identity: str) -> str:
    return hashlib.sha256(f"{session_id}:{identity}".encode()).hexdigest()


def represented(db: Database, session_id: str, events: list[dict[str, Any]]) -> None:
    from . import delivery_telemetry
    from .live_representation import translate_sdk_event
    for event in events:
        if event.get("type") == "bridge.delivery_observation":
            diagnostic = event.get("data")
            if (
                isinstance(diagnostic, dict) and not event.get("agentId")
                and not diagnostic.get("agentId") and not diagnostic.get("parentToolCallId")
                and not is_nested_event_data(diagnostic)
            ):
                delivery_telemetry.represented(session_id, diagnostic)
            continue
        # Native identities survive redelivery and daemon restart. Missing
        # identity cannot safely deduplicate a historical transition.
        identity = event.get("id")
        if not isinstance(identity, str) or not identity:
            continue
        data = dict(event.get("data") or {})
        if event.get("agentId"):
            data["agentId"] = event["agentId"]
        for kind, translated in translate_sdk_event(event["type"], data):
            normalized(db, session_id, kind, translated, f"native:{identity}:{kind}")


def normalized(
    db: Database, session_id: str, event: str, data: dict[str, Any], key: str, *, timestamp: float | None = None,
) -> None:
    kind = _EVENTS.get(event)
    if kind is not None:
        StatusStore(db).publish(
            session_id, kind, source_key(session_id, key),
            scope="nested" if is_nested_event_data(data) else "root",
            observed_at=timestamp,
            evidence="historical_transition",
        )


def question_snapshot(db: Database, session_id: str, observation: QuestionObservation) -> None:
    if not observation.requests:
        return
    # Snapshot freshness is qualified by its owner before calling this adapter.
    # Request content, choices, schema and answer authority stay in /questions.
    identities = [(q.family, q.request_id) for q in observation.requests]
    key = hashlib.sha256(json.dumps([session_id, observation.generation, identities], sort_keys=True).encode()).hexdigest()
    StatusStore(db).publish(
        session_id, "question_pending", f"questions:{key}", evidence="fresh_native_snapshot",
    )
