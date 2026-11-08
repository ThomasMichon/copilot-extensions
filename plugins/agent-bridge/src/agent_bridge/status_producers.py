"""Metadata-only adapters at authoritative event boundaries; no transcript reads."""

from __future__ import annotations

import hashlib
import json

from .events import is_nested_event_data
from .status_store import StatusStore

_EVENTS = {
    "ask_user_request": "question_pending",
    "permission_request": "permission_pending",
    "turn_complete": "idle",
    "error": "error",
}


def represented(db, session_id: str, events: list[dict]) -> None:
    from .live_representation import translate_sdk_event
    for event in events:
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


def normalized(db, session_id: str, event: str, data: dict, key: str, *, timestamp=None) -> None:
    kind = _EVENTS.get(event)
    if kind is not None:
        StatusStore(db).publish(
            session_id, kind, hashlib.sha256(f"{session_id}:{key}".encode()).hexdigest(),
            scope="nested" if is_nested_event_data(data) else "root",
            observed_at=timestamp,
            evidence="historical_transition",
        )


def question_snapshot(db, session_id: str, observation) -> None:
    if not observation.requests:
        return
    # Snapshot freshness is qualified by its owner before calling this adapter.
    # Request content, choices, schema and answer authority stay in /questions.
    identities = [(q.family, q.request_id) for q in observation.requests]
    key = hashlib.sha256(json.dumps([session_id, observation.generation, identities], sort_keys=True).encode()).hexdigest()
    StatusStore(db).publish(
        session_id, "question_pending", f"questions:{key}", evidence="fresh_native_snapshot",
    )
