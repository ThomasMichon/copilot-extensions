"""Union existing registration and workspace owners without a target registry."""

from __future__ import annotations

import sqlite3
from typing import Any

CAPABILITY = "target_inventory"


async def inventory(request: Any, *, refresh: bool = False) -> dict[str, Any]:
    """Return addressable targets and honest, independent source observations."""
    from .routes import worktrees
    from .routes.live_sessions import _to_info
    from .routes.sessions import _session_info

    manager = request.app.state.session_manager
    targets: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    managed: dict[str, dict[str, Any]] = {}
    for session in manager.list_sessions():
        info = _session_info(session, manager).model_dump(mode="json")
        if info["status"] in {"stopped", "ended", "failed"}:
            continue
        item = {
            "kind": "managed", "handle": info["session_id"],
            "sources": ["managed"], "session": info,
        }
        managed[info["session_id"]] = item
        targets.append(item)
    sources["managed"] = {"availability": "available"}

    index = getattr(manager, "_host_index", None)
    if index is None:
        sources["session_host"] = {"availability": "unavailable", "reason": "inventory_unavailable"}
    else:
        try:
            hosts = index.locked_inventory()
            sources["session_host"] = {
                "availability": "available" if hosts.storage_complete else "unavailable",
                "reason": None if hosts.storage_complete else "inventory_missing",
            }
            for record in hosts.records:
                if not manager._rec_host_alive(record):
                    continue
                represented = managed.get(record.session_id)
                cwd = record.extra.get("cwd")
                if represented is not None and (
                    not cwd or represented["session"]["target_dir"] == cwd
                ):
                    represented["sources"].append("session_host")
                    continue
                targets.append({
                    "kind": "session_host", "handle": record.session_id,
                    "sources": ["session_host"],
                    "registration": {
                        "session_id": record.session_id, "cwd": cwd,
                        "boundary": record.boundary,
                        "liveness": "observed" if record.boundary == "local" else "unverified",
                    },
                })
        except (OSError, ValueError, RuntimeError) as exc:
            sources["session_host"] = {"availability": "unavailable", "reason": type(exc).__name__}

    try:
        registrations = manager.db.list_live_sessions(include_dead=False)
        for row in registrations:
            targets.append({
                "kind": "live", "handle": row["session_id"], "sources": ["extension"],
                "registration": _to_info(row).model_dump(mode="json"),
            })
        sources["extension"] = {"availability": "available"}
    except sqlite3.Error as exc:
        sources["extension"] = {"availability": "unavailable", "reason": type(exc).__name__}

    # This is the ordinary app-owned discovery cache, with its existing freshness
    # and incomplete-source evidence; listing never hydrates or launches targets.
    workspaces = await worktrees.list_worktrees(request, render=False, refresh=refresh)
    sources["worktrees"] = workspaces["discovery"]
    resolver = request.app.state.resolver
    for owner, entries in workspaces["groups"].items():
        config = resolver.agents.get(owner)
        for entry in entries:
            targets.append({
                "kind": "worktree", "handle": entry["id"], "sources": ["worktrees"],
                "owner": owner, "project": config.project if config is not None else None,
                "worktree": entry,
            })
    return {"version": 1, "targets": targets, "sources": sources}
