"""Qualify the original complete keyed-native journal before closing a send."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .native_keyed_admission import KeyedNativePrompt, stored_receipt
from .native_send_admission import NativeSendPrompt

if TYPE_CHECKING:
    from .db import Database

TURN_LIMIT = 100
STATE_EVENT_LIMIT = 1000


def require_original_history(
    db: Database, conn: sqlite3.Connection, session_id: str, request: NativeSendPrompt,
) -> None:
    """Accept only a complete original workspace and same-native keyed turn history."""
    error = "native_send_history_unproven"
    session = conn.execute(
        "SELECT acp_session_id,target_type FROM sessions WHERE id=?", (session_id,),
    ).fetchone()
    origins = conn.execute(
        "SELECT state,native_session_id,worktree_json FROM workspace_embodiments "
        "WHERE session_id=?", (session_id,),
    ).fetchall()
    if (
        session is None or session["acp_session_id"] != request.expected_native_session_id
        or session["target_type"] != "local" or len(origins) != 1
        or origins[0]["state"] != "complete"
        or origins[0]["native_session_id"] != request.expected_native_session_id
    ):
        raise ValueError(error)
    rows = conn.execute(
        "SELECT turn_index,prompt,acquisition_delivery_id,native_admission_json "
        "FROM turns WHERE session_id=? ORDER BY turn_index LIMIT ?",
        (session_id, TURN_LIMIT + 1),
    ).fetchall()
    if not rows or len(rows) > TURN_LIMIT:
        raise ValueError(error)
    if conn.execute(
        "SELECT 1 FROM delivery_cursor_invalidations WHERE session_id=? LIMIT 1", (session_id,),
    ).fetchone() is not None:
        raise ValueError(error)
    starts = conn.execute(
        "SELECT data_json FROM events "
        "WHERE session_id=? AND event_type='session_state_changed' ORDER BY event_id LIMIT ?",
        (session_id, STATE_EVENT_LIMIT + 1),
    ).fetchall()
    if len(starts) > STATE_EVENT_LIMIT:
        raise ValueError(error)
    try:
        workspace = json.loads(origins[0]["worktree_json"])
        indices = set()
        for event in starts:
            data = json.loads(event["data_json"])
            if not isinstance(data, dict):
                raise ValueError(error)
            if data.get("status") == "running":
                index = data["turn_index"]
                if type(index) is not int:
                    raise ValueError(error)
                indices.add(index)
        if indices != set(range(len(rows))):
            raise ValueError(error)
        for index, row in enumerate(rows):
            if row["turn_index"] != index or row["prompt"] == request.prompt:
                raise ValueError(error)
            saved = json.loads(row["native_admission_json"])
            facts = saved["facts"]
            delivery = row["acquisition_delivery_id"]
            if not isinstance(delivery, str):
                raise ValueError(error)
            original: KeyedNativePrompt | NativeSendPrompt
            if delivery.startswith("ordinary-native:v1:"):
                original = KeyedNativePrompt(
                    invocation_key=delivery.removeprefix("ordinary-native:v1:"),
                    expected_native_session_id=request.expected_native_session_id,
                    prompt=row["prompt"], binding=facts["binding"],
                )
                binding = original.binding
                if (
                    binding.expected_turn_index != index
                    or any(getattr(binding, key) != workspace[field] for key, field in (
                        ("worktree_id", "id"), ("project", "repo"), ("path", "path"),
                        ("creation_nonce", "creation_nonce"),
                    ))
                    or index == 0 and binding.origin_kind != "created"
                ):
                    raise ValueError(error)
            elif index > 0 and delivery.startswith("native-send:v1:"):
                original = NativeSendPrompt(
                    invocation_key=delivery.removeprefix("native-send:v1:"),
                    expected_native_session_id=request.expected_native_session_id,
                    prompt=row["prompt"], caller_id=facts.get("caller_id"),
                    no_resume=facts.get("no_resume", False),
                )
            else:
                raise ValueError(error)
            if stored_receipt(db, session_id, original, connection=conn) is None:
                raise ValueError(error)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(error) from exc
