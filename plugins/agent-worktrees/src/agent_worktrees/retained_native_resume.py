"""Pairing-specific checks inside the existing native process-creation fence."""

from __future__ import annotations

import json
import os

ENVIRONMENT_KEY = "AGENT_WORKTREES_RETAINED_NATIVE_RESUME"


def require_record(record, native_session_id: str | None) -> None:
    raw = os.environ.get(ENVIRONMENT_KEY)
    if raw is None:
        return
    from . import retained_record_writer

    try:
        value = json.loads(raw)
        if not (
            isinstance(value, dict)
            and set(value) == {"version", "operation_id", "writer", "head"}
            and type(value["version"]) is int and value["version"] == 1
            and isinstance(value["operation_id"], str) and value["operation_id"].strip()
            and len(value["operation_id"]) <= 1024
            and isinstance(value["head"], dict) and set(value["head"]) == {"session_id", "revision"}
            and type(value["head"]["revision"]) is int and value["head"]["revision"] > 0
            and value["head"]["session_id"] == native_session_id
            and native_session_id is not None
        ):
            raise ValueError("Invalid retained native recovery intent")
        scope = retained_record_writer._validate(value["writer"])
        if any(getattr(record, name) != scope[name] for name in (
            "worktree_id", "worktree_path", "creation_nonce",
        )) or record.repo != scope["project"] or (
            record.resolved_head_session != native_session_id
            or record.head_revision != value["head"]["revision"]
        ):
            raise ValueError("Retained native recovery workspace or head revision changed")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Retained native recovery refused: {exc}") from exc
