"""Union existing registration and workspace owners without a target registry."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

CAPABILITY = "target_inventory"


def host_observation_key(registration: object) -> tuple[int, str, str] | None:
    if not isinstance(registration, dict):
        return None
    pid = registration.get("host_pid")
    birth = registration.get("host_process_start_time")
    cwd = registration.get("cwd")
    if (
        registration.get("boundary") != "local" or type(pid) is not int or pid <= 0
        or not isinstance(birth, str) or not birth or not isinstance(cwd, str) or not cwd
    ):
        return None
    try:
        return pid, birth, os.path.normcase(os.path.realpath(cwd))
    except (OSError, ValueError):
        return None


def same_host_observation(first: object, second: object) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    first_key, second_key = host_observation_key(first), host_observation_key(second)
    if first_key is not None and second_key is not None:
        return first_key == second_key
    instance, pid, birth = (
        first.get("instance_id"), first.get("host_pid"), first.get("host_process_start_time"),
    )
    return bool(
        first.get("boundary") == second.get("boundary") == "local"
        and isinstance(instance, str) and instance and instance == second.get("instance_id")
        and type(pid) is int and pid > 0 and type(second.get("host_pid")) is int
        and pid == second.get("host_pid")
        and isinstance(birth, str) and birth and birth == second.get("host_process_start_time")
    )


async def inventory(
    request: Any, *, refresh: bool = False, local_only: bool = False,
) -> dict[str, Any]:
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
        observed = getattr(getattr(session, "client", None), "is_running", None)
        info["session_live"] = observed if type(observed) is bool else None
        item = {
            "kind": "managed", "handle": info["session_id"],
            "sources": ["managed"], "session": info,
        }
        managed[info["session_id"]] = item
        targets.append(item)
    sources["managed"] = {"availability": "available"}

    from .session_host.registered_inventory import registered_hosts

    standalone, sources["standalone_session_host"] = registered_hosts()
    observed_scopes: dict[tuple[int, str], set[str]] = {}
    for item in standalone:
        key = host_observation_key(item["registration"])
        if key is not None:
            observed_scopes.setdefault(key[:2], set()).add(key[2])
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
                birth = record.extra.get("host_process_start")
                scopes = observed_scopes.get((record.host_pid, birth), set()) if record.boundary == "local" else set()
                if not cwd:
                    if len(scopes) == 1:
                        cwd = next(iter(scopes))
                    elif not scopes and represented is not None:
                        cwd = represented["session"]["target_dir"]
                registration = {
                    "session_id": record.session_id, "cwd": cwd,
                    "host_pid": record.host_pid, "host_process_start_time": birth,
                    "boundary": record.boundary,
                    "liveness": "observed" if record.boundary == "local" else "unverified",
                }
                target_scope = represented["session"]["target_dir"] if represented is not None else None
                same_scope = bool(
                    isinstance(cwd, str) and isinstance(target_scope, str)
                    and os.path.normcase(os.path.realpath(cwd)) == os.path.normcase(os.path.realpath(target_scope))
                )
                if represented is not None and same_scope:
                    represented["sources"].append("session_host")
                    represented["registration"] = registration
                    continue
                item = {
                    "kind": "session_host", "handle": record.session_id,
                    "sources": ["session_host"],
                    "registration": registration,
                }
                targets.append(item)
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

    for item in standalone:
        registration = item["registration"]
        represented = next((
            target for target in targets
            if same_host_observation(registration, target.get("registration"))
        ), None)
        if represented is not None:
            represented["sources"].extend(item["sources"])
            represented["registration"] = registration
        else:
            targets.append(item)

    # This is the ordinary app-owned discovery cache, with its existing freshness
    # and incomplete-source evidence; listing never hydrates or launches targets.
    if local_only:
        from .bridge_observations import cached_local_worktrees
        workspaces = cached_local_worktrees(request)
    else:
        workspaces = await worktrees.list_worktrees(request, render=False, refresh=refresh)
    sources["worktrees"] = workspaces["discovery"]
    resolver = request.app.state.resolver
    from .routes.worktree_refresh import worktree_sources
    configured_sources = worktree_sources(resolver)
    for owner, entries in workspaces["groups"].items():
        config = configured_sources.get(owner)
        for entry in entries:
            targets.append({
                "kind": "worktree", "handle": entry["id"], "sources": ["worktrees"],
                "owner": owner if owner in resolver.agents else None,
                "project": config.project if config is not None else None,
                "worktree": entry,
                "observation": sources["worktrees"].get("sources", {}).get(owner, sources["worktrees"]),
            })
    return {"version": 1, "targets": targets, "sources": sources}
