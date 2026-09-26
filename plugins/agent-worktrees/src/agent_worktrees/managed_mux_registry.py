"""Read-only view of Worktree Manager's mux-mapping registry."""

from __future__ import annotations

import json

from . import front_door_cli


def live_mux_session_name(
    worktree_id: str,
    *,
    project: str | None,
    session_name: str | None = None,
) -> str | None:
    """Return the live Manager-owned mux session name for this worktree, if any."""
    if not worktree_id or not project:
        return None
    try:
        raw = (front_door_cli._worktree_manager_root() / "mux-mapping.json").read_text(
            encoding="utf-8"
        )
        data = json.loads(raw)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    current = None
    current_revision = -1
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("project") != project or item.get("worktree_id") != worktree_id:
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
    if session_name and mux_session != session_name:
        return None
    return mux_session
