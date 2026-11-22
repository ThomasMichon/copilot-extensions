"""Read-only conversation projection over the existing Bridge inventory owners."""

from __future__ import annotations

import ntpath
import posixpath
from typing import Any

CAPABILITY = "bridge_inventory"
_TERMINAL = {"stopped", "ended", "failed", "expired", "taken-over", "closed"}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _scope_path(value: str | None) -> str | None:
    if value is None:
        return None
    if ntpath.splitdrive(value)[0] or "\\" in value:
        return ntpath.normcase(ntpath.normpath(value))
    return posixpath.normpath(value)


def _identity(target: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    kind = target.get("kind")
    row = (
        target.get("worktree")
        if kind == "worktree"
        else (target.get("session") if kind == "managed" else target.get("registration"))
    )
    row = row if isinstance(row, dict) else {}
    project = _text(target.get("project")) or _text(row.get("project"))
    machine = _text(row.get("machine"))
    if kind == "managed" and row.get("target_type") not in {None, "local"}:
        machine = _text(row.get("target_host")) or machine
    workspace = _text(row.get("id" if kind == "worktree" else "worktree_id"))
    directory = _text(
        row.get("path" if kind == "worktree" else ("target_dir" if kind == "managed" else "cwd"))
    )
    return project, machine, workspace, directory


def _native(item: dict[str, Any]) -> str | None:
    row = item.get("session") or item.get("registration") or item.get("worktree") or {}
    return (
        _text(row.get("durable_session_id"))
        or _text(
            row.get(
                "session_id" if item.get("kind") == "live" else "acp_session_id",
            )
        )
        or (_text(row.get("session_id")) if item.get("kind") == "session_host" else None)
    )


def _matches(first: dict[str, Any], second: dict[str, Any]) -> bool:
    a, b = _identity(first), _identity(second)
    if any(x and y and x != y for x, y in zip(a[:2], b[:2], strict=True)):
        return False
    if a[3] and b[3] and _scope_path(a[3]) != _scope_path(b[3]):
        return False
    if a[2] and b[2]:
        return a[2] == b[2]
    return bool(
        (_native(first) and _native(first) == _native(second))
        or (a[1] and b[1] and a[3] and b[3] and (ntpath.isabs(a[3]) or posixpath.isabs(a[3])))
    )


def _source_complete(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if "availability" in value:
        return value["availability"] == "available"
    return value.get("completeness") == "complete" and value.get("freshness") == "fresh"


def _project(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    workspace = next((item for item in group if item.get("kind") == "worktree"), None)
    metadata = workspace["worktree"] if workspace else {}
    candidates = [item for item in group if item.get("kind") != "worktree"]
    live = [
        item
        for item in candidates
        if (item.get("session", item.get("registration", {})).get("status") not in _TERMINAL)
    ]
    managed = [item["session"] for item in candidates if item.get("kind") == "managed"]
    registrations = [
        item["registration"]
        for item in live
        if item.get("kind") == "live" and isinstance(item.get("registration"), dict)
    ]
    hosts = [
        item["registration"]
        for item in live
        if item.get("kind") == "session_host" and isinstance(item.get("registration"), dict)
    ]
    managed.sort(key=lambda row: row.get("status") not in _TERMINAL, reverse=True)
    session = managed[0] if managed else {}
    registration = registrations[0] if registrations else {}
    host = hosts[0] if hosts else {}
    progress = registration.get("latest_progress") or session.get("latest_progress") or {}
    progress = progress if isinstance(progress, dict) else {}
    count = metadata.get("session_count")
    count = count if type(count) is int and count >= 0 else 0
    process_live = any(metadata.get(key) is True for key in ("bound_live", "session_live"))
    if (
        workspace
        and not count
        and not process_live
        and not any(_native(item) for item in candidates)
    ):
        return None
    if not workspace and not live:
        return None
    project, machine, worktree_id, directory = _identity(workspace or group[0])
    for item in group:
        p, m, w, d = _identity(item)
        project, machine = project or p, machine or m
        worktree_id, directory = worktree_id or w, directory or d
    project = project or _text(registration.get("repo"))
    native = (
        _text(registration.get("session_id"))
        or _text(session.get("durable_session_id"))
        or _text(session.get("acp_session_id"))
        or _text(host.get("session_id"))
        or _text(metadata.get("durable_session_id"))
    )
    positive = (
        bool(registrations)
        or any(row.get("liveness") in {"observed", "active", "stalled"} for row in hosts)
        or any(
            row.get("status") not in _TERMINAL and row.get("liveness") in {"active", "stalled"}
            for row in managed
        )
        or process_live
    )
    negative = metadata.get("bound_live") is False or (
        bool(session) and session.get("status") in _TERMINAL
    )
    is_live = True if positive else False if negative else None
    anchor = not worktree_id or metadata.get("kind") in {"anchor", "bare"}
    if anchor and is_live is not True:
        return None
    status = "running" if is_live else "dormant" if is_live is False else "retained"
    transport = (
        _text(session.get("target_type"))
        or _text(host.get("boundary"))
        or _text(metadata.get("transport"))
        or "unknown"
    )
    name = _text(session.get("agent_name")) or _text(metadata.get("agent_name")) or ""
    namespace = name.split(":", 1)[0] if ":" in name else "local"
    handle = worktree_id or _text(group[0].get("handle"))
    identities = {
        value
        for value in [
            *[_text(row.get("session_id")) for row in registrations],
            *[
                _text(row.get("durable_session_id")) or _text(row.get("acp_session_id"))
                for row in managed
                if not live or row.get("status") not in _TERMINAL
            ],
        ]
        if value
    }
    ambiguous = len(identities) > 1
    controlled = not ambiguous and (
        bool(registration) or any(row.get("status") not in _TERMINAL for row in managed)
    )
    return {
        "handle": handle,
        "session_id": None if ambiguous else native,
        "managed_session_id": None if ambiguous else _text(session.get("session_id")),
        "observed_session_ids": sorted(identities),
        "worktree_id": worktree_id,
        "project": project,
        "machine": machine,
        "namespace": namespace,
        "directory": directory,
        "location_kind": "worktree" if not anchor else "anchor" if directory else "unknown",
        "session_count": max(count, 1 if candidates or process_live else 0),
        "status": status,
        "live": is_live,
        "hosting": _text(session.get("mode"))
        or (
            "session_host"
            if host
            else "mux"
            if metadata.get("mux_session") is True
            else "cli"
            if registration
            else "unknown"
        ),
        "transport": transport,
        "control": "registered" if controlled else "unavailable",
        "control_reason": None
        if controlled
        else "identity_ambiguous"
        if ambiguous
        else (
            "registration_missing"
            if is_live
            else "not_running"
            if is_live is False
            else "liveness_unknown"
        ),
        "title": _text(metadata.get("title")) or _text(session.get("name")),
        "disposition": {
            "follow_up": metadata.get("follow_up"),
            "summary": metadata.get("summary"),
            "activity": metadata.get("activity") or _text(progress.get("summary")),
        },
        "creator_session_id": _text(session.get("caller_session_id")),
        "creator_worktree": _text(session.get("creator_worktree")),
        "caller_worktree": _text(session.get("caller_id")),
        "controller": registration.get("driven_by") or host.get("controller"),
        "sources": list(
            dict.fromkeys(
                source
                for item in group
                for source in item.get(
                    "sources",
                    ["worktrees"] if item.get("kind") == "worktree" else [],
                )
            )
        ),
    }


def project_bridges(
    targets: list[dict[str, Any]],
    sources: dict[str, Any],
    *,
    project: str | None = None,
    machine: str | None = None,
    namespace: str | None = None,
    status: str | None = None,
    live: bool = False,
) -> dict[str, Any]:
    """Keep assignment, liveness and control separate without hydrating metadata."""
    groups: list[list[dict[str, Any]]] = []
    workspaces: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    by_workspace: dict[str, list[list[dict[str, Any]]]] = {}
    by_native: dict[str, list[list[dict[str, Any]]]] = {}
    by_path: dict[tuple[str, str], list[list[dict[str, Any]]]] = {}

    def path_key(item: dict[str, Any]) -> tuple[str, str] | None:
        _p, machine, _w, directory = _identity(item)
        if machine and directory and (ntpath.isabs(directory) or posixpath.isabs(directory)):
            normalized = _scope_path(directory)
            if normalized:
                return machine, normalized
        return None

    def index(item: dict[str, Any], group: list[dict[str, Any]]) -> None:
        for value, mapping in ((_identity(item)[2], by_workspace), (_native(item), by_native)):
            if value and not any(existing is group for existing in mapping.get(value, [])):
                mapping.setdefault(value, []).append(group)
        key = path_key(item)
        if key and not any(existing is group for existing in by_path.get(key, [])):
            by_path.setdefault(key, []).append(group)

    for item in targets:
        if item.get("kind") == "worktree":
            p, m, w, d = _identity(item)
            key = p, m, w, _scope_path(d)
            if key in workspaces:
                workspaces[key].append(item)
            else:
                group = [item]
                groups.append(group)
                workspaces[key] = group
                index(item, group)
    for item in targets:
        if item.get("kind") == "worktree":
            continue
        candidates = {
            id(group): group
            for group in [
                *by_workspace.get(_identity(item)[2] or "", []),
                *by_native.get(_native(item) or "", []),
                *by_path.get(path_key(item) or ("", ""), []),
            ]
        }
        matches = [
            group for group in candidates.values() if any(_matches(item, other) for other in group)
        ]
        if len(matches) == 1:
            group = matches[0]
            group.append(item)
        else:
            group = [item]
            groups.append(group)
        index(item, group)
    bridges = []
    for group in groups:
        row = _project(group)
        if row is None:
            continue
        if any(
            value is not None and row[key] != value
            for key, value in (
                ("project", project),
                ("machine", machine),
                ("namespace", namespace),
                ("status", status),
            )
        ):
            continue
        if live and row["live"] is not True:
            continue
        bridges.append(row)
    return {
        "version": 1,
        "bridges": bridges,
        "sources": sources,
        "complete": bool(sources)
        and all(_source_complete(value) for value in sources.values())
        and not any(row["control_reason"] == "identity_ambiguous" for row in bridges),
    }


async def inventory(request: Any, *, local_only: bool = False, **scope: Any) -> dict[str, Any]:
    """Reuse cached discovery and retained managed metadata, never native history."""
    from . import target_inventory
    from .routes.sessions import _session_info

    catalog = await target_inventory.inventory(request, refresh=False)
    targets = list(catalog["targets"])
    known = {item["handle"] for item in targets if item.get("kind") == "managed"}
    manager = request.app.state.session_manager
    for session in manager.list_sessions():
        if session.session_id not in known:
            targets.append(
                {
                    "kind": "managed",
                    "handle": session.session_id,
                    "sources": ["managed"],
                    "session": _session_info(session, manager).model_dump(mode="json"),
                }
            )
    resolver = getattr(request.app.state, "resolver", None)
    host = getattr(getattr(resolver, "_local_machine", None), "key", None)
    if host is not None:
        located = []
        for item in targets:
            field = "session" if item.get("kind") == "managed" else "registration"
            row = item.get(field) or {}
            local = (item.get("kind") == "managed" and row.get("target_type") == "local") or (
                item.get("kind") == "session_host" and row.get("boundary") == "local"
            )
            located.append(
                {**item, field: {**row, "machine": host}}
                if local and not row.get("machine")
                else item
            )
        targets = located
    if local_only:
        targets = [
            item
            for item in targets
            if item.get("kind") != "worktree" or (host is not None and _identity(item)[1] == host)
        ]
    sources = dict(catalog["sources"])
    if local_only and host is None:
        sources["local_identity"] = {"availability": "unavailable", "reason": "identity_unknown"}
    report = project_bridges(targets, sources, **scope)
    report["host"] = host
    for row in report["bridges"]:
        row["route"] = {
            "host": host,
            "handle": row["managed_session_id"] or row["session_id"] or row["handle"],
            "kind": "managed"
            if row["managed_session_id"]
            else ("live" if row["control"] == "registered" else row["location_kind"]),
        }
    return report
