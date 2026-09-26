"""Read-only view of Worktree Manager's mux-mapping registry."""

from __future__ import annotations

import json

from . import front_door_cli


def _read_registry() -> list[dict]:
    try:
        raw = (front_door_cli._worktree_manager_root() / "mux-mapping.json").read_text(
            encoding="utf-8"
        )
        data = json.loads(raw)
    except (OSError, TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _latest_live_entry(matches) -> dict | None:
    current = None
    current_revision = -1
    for item in matches:
        if not isinstance(item, dict):
            continue
        revision = item.get("mapping_revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            continue
        if revision >= current_revision:
            current = item
            current_revision = revision
    mux_session = current.get("mux_session") if isinstance(current, dict) else None
    if current is None or current.get("live") is not True or not isinstance(mux_session, str):
        return None
    return current


def live_mux_session_name(
    worktree_id: str,
    *,
    project: str | None,
    session_name: str | None = None,
) -> str | None:
    """Return the live Manager-owned mux session name for this worktree, if any."""
    if not worktree_id or not project:
        return None
    current = _latest_live_entry(
        item
        for item in _read_registry()
        if item.get("project") == project and item.get("worktree_id") == worktree_id
    )
    mux_session = current.get("mux_session") if isinstance(current, dict) else None
    if session_name and mux_session != session_name:
        return None
    return mux_session


def live_mapping_for_session(session_name: str) -> dict | None:
    """Return the current live Manager-owned mapping for ``session_name``, if any."""
    if not session_name:
        return None
    return _latest_live_entry(
        item for item in _read_registry() if item.get("mux_session") == session_name
    )
