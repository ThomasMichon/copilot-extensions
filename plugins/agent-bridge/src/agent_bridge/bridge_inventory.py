"""Read-only conversation projection over the existing Bridge inventory owners."""

from __future__ import annotations

import ntpath
import posixpath
from typing import Any

CAPABILITY = "bridge_inventory"
_TERMINAL = {"stopped", "ended", "failed", "expired", "taken-over", "closed"}


class BridgeSelectionError(ValueError):
    """An exact conversation selector is absent, ambiguous or unconfirmed."""

    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(reason)


def select_bridge(report: dict[str, Any], handle: str) -> dict[str, Any]:
    """Select one projected conversation, never a profile or potential placement."""
    if not isinstance(handle, str) or not handle.strip():
        raise BridgeSelectionError(400, "invalid_bridge_selector")
    keys = (
        "handle",
        "worktree_id",
        "session_id",
        "managed_session_id",
        "registered_session_id",
        "retained_managed_session_id",
    )
    matches = [
        row
        for row in report["bridges"]
        if any(row.get(key) == handle for key in keys)
        or any(
            isinstance(row.get(key), list) and handle in row[key]
            for key in ("observed_session_ids", "identity_aliases")
        )
    ]
    if len(matches) > 1 or any(
        row.get("control_reason") in ("identity_ambiguous", "retained_head_unknown")
        for row in matches
    ):
        raise BridgeSelectionError(409, "ambiguous_bridge_selector")
    if not matches:
        if not report["complete"]:
            raise BridgeSelectionError(503, "bridge_inventory_incomplete")
        raise BridgeSelectionError(404, "bridge_not_found")
    return {
        "version": 1,
        "bridge": matches[0],
        "sources": report["sources"],
        "complete": report["complete"],
    }


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
    workspace = _text(row.get("id" if kind == "worktree" else "worktree_id"))
    directory = _text(
        row.get("path" if kind == "worktree" else ("target_dir" if kind == "managed" else "cwd"))
    )
    return project, machine, workspace, directory


def _native(item: dict[str, Any]) -> str | None:
    row = item.get("session") or item.get("registration") or item.get("worktree") or {}
    if item.get("kind") == "managed":
        native = _text(row.get("acp_session_id"))
        durable = _text(row.get("durable_session_id"))
        return native or (durable if durable != row.get("session_id") else None)
    return _text(row.get("durable_session_id")) or _text(
        row.get(
            "session_id" if item.get("kind") == "live" else "acp_session_id",
        )
    )


def _scope_conflicts(first: dict[str, Any], second: dict[str, Any]) -> bool:
    a, b = _identity(first), _identity(second)
    return any(
        x and y and x != y
        for x, y in zip(
            (*a[:3], _scope_path(a[3])),
            (*b[:3], _scope_path(b[3])),
            strict=True,
        )
    )


def _matches(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if _scope_conflicts(first, second):
        return False
    a, b = _identity(first), _identity(second)
    if a[2] and b[2]:
        return a[2] == b[2]
    native_a, native_b = _native(first), _native(second)
    if native_a and native_b:
        return native_a == native_b
    return bool(a[1] and b[1] and a[3] and b[3] and (ntpath.isabs(a[3]) or posixpath.isabs(a[3])))


def _matches_group(item: dict[str, Any], group: list[dict[str, Any]]) -> bool:
    if any(_scope_conflicts(item, other) for other in group) or not any(
        _matches(item, other) for other in group
    ):
        return False
    workspace, native = _identity(item)[2], _native(item)
    return bool(
        not native
        or any(workspace and workspace == _identity(other)[2] for other in group)
        or not any(_native(other) and _native(other) != native for other in group)
    )


def _source_complete(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if "availability" in value:
        return bool(value["availability"] == "available")
    return bool(value.get("completeness") == "complete" and value.get("freshness") == "fresh")


def _project(group: list[dict[str, Any]], *, owner_current: bool) -> dict[str, Any] | None:
    workspace = next((item for item in group if item.get("kind") == "worktree"), None)
    metadata = workspace["worktree"] if workspace else {}
    candidates = [item for item in group if item.get("kind") != "worktree"]
    live = [
        item
        for item in candidates
        if (item.get("session", item.get("registration", {})).get("status") not in _TERMINAL)
    ]
    managed_items = [item for item in candidates if item.get("kind") == "managed"]
    managed = [item["session"] for item in managed_items]
    registrations = [
        item["registration"]
        for item in live
        if item.get("kind") == "live" and isinstance(item.get("registration"), dict)
    ]
    hosts = [
        item["registration"]
        for item in live
        if (
            item.get("kind") == "session_host"
            or (item.get("kind") == "managed" and "session_host" in item.get("sources", []))
        )
        and isinstance(item.get("registration"), dict)
    ]
    active_managed = [
        item
        for item in managed_items
        if item["session"].get("status") not in _TERMINAL
        and not item["session"].get("read_only", False)
    ]
    identities = {
        value
        for value in [
            *[_text(row.get("session_id")) for row in registrations],
            *[_native(item) for item in active_managed],
            *[_native(item) for item in live if item.get("kind") == "session_host"],
        ]
        if value
    }
    ambiguous = len(identities) > 1
    owner_head = _text(metadata.get("last_session_id"))
    historical = {value for item in managed_items if (value := _native(item))}
    native = (
        next(iter(identities))
        if len(identities) == 1
        else (owner_head or (next(iter(historical)) if len(historical) == 1 else None))
    )
    matching = [item for item in managed_items if _native(item) == native] if native else []
    current: dict[str, Any] = next(
        (item["session"] for item in matching if item in active_managed), {}
    )
    if not current and not native and len(active_managed) == 1:
        current = active_managed[0]["session"]
    session = current or (matching[0]["session"] if matching else {})
    registration: dict[str, Any] = next(
        (row for row in registrations if row.get("session_id") == native), {}
    )
    host = hosts[0] if hosts else {}
    progress = registration.get("latest_progress") or session.get("latest_progress") or {}
    progress = progress if isinstance(progress, dict) else {}
    count = metadata.get("session_count")
    count = count if type(count) is int and count >= 0 else 0
    bound_live = metadata.get("session_bound_live") if owner_current else None
    lock_live = metadata.get("session_lock_live") if owner_current else None
    process_live = bound_live is True or lock_live is True
    if (
        not workspace
        and not live
        and not any(_identity(item)[2] and _native(item) for item in managed_items)
    ):
        return None
    project, machine, worktree_id, directory = _identity(workspace or group[0])
    for item in group:
        p, m, w, d = _identity(item)
        project, machine = project or p, machine or m
        worktree_id, directory = worktree_id or w, directory or d
    project = project or _text(registration.get("repo"))
    standalone_native = next(
        (_native(item) for item in candidates if item.get("kind") == "session_host"), None
    )
    native = native or standalone_native or _text(metadata.get("acp_session_id"))
    positive = (
        bool(registrations)
        or any(row.get("liveness") in {"observed", "active", "stalled"} for row in hosts)
        or any(
            row.get("status") not in _TERMINAL
            and (row.get("session_live") is True or row.get("liveness") in {"active", "stalled"})
            for row in managed
        )
        or process_live
    )
    if (
        workspace
        and not count
        and not positive
        and not owner_head
        and not any(_native(item) for item in candidates)
    ):
        return None
    negative = bound_live is False or (
        bool(session)
        and (session.get("status") in _TERMINAL or session.get("session_live") is False)
    )
    is_live = True if positive else False if negative else None
    if not workspace and worktree_id and owner_current and is_live is not True:
        return None
    anchor = not worktree_id or metadata.get("kind") in {"anchor", "bare"}
    if anchor and is_live is not True:
        return None
    status = "running" if is_live else "dormant" if is_live is False else "retained"
    mode_session = current or (session if not registration else {})
    transport = (
        _text(mode_session.get("target_type"))
        or _text(host.get("boundary"))
        or _text(metadata.get("transport"))
        or "unknown"
    )
    name = _text(mode_session.get("agent_name")) or _text(metadata.get("agent_name")) or ""
    venue = registration.get("venue")
    venue = venue if isinstance(venue, dict) else {}
    namespace = name.split(":", 1)[0] if ":" in name else _text(venue.get("kind")) or "local"
    handle = worktree_id or _text(group[0].get("handle"))
    controlled = not ambiguous and bool(registration or current)
    return {
        "handle": handle,
        "session_id": None if ambiguous else native,
        "managed_session_id": None if ambiguous else _text(current.get("session_id")),
        "registered_session_id": None if ambiguous else _text(registration.get("session_id")),
        "retained_managed_session_id": (_text(session.get("session_id")) if not current else None),
        "observed_session_ids": sorted(identities),
        "worktree_id": worktree_id,
        "project": project,
        "machine": machine,
        "namespace": namespace,
        "directory": directory,
        "location_kind": "worktree" if not anchor else "anchor" if directory else "unknown",
        "session_count": max(count, 1 if candidates or process_live or owner_head else 0),
        "status": status,
        "live": is_live,
        "hosting": _text(mode_session.get("mode"))
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
        "transport_host": _text(mode_session.get("target_host")),
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
    for item in sorted(
        (item for item in targets if item.get("kind") != "worktree"),
        key=lambda item: _native(item) is None,
    ):
        candidates = {
            id(group): group
            for group in [
                *by_workspace.get(_identity(item)[2] or "", []),
                *by_native.get(_native(item) or "", []),
                *by_path.get(path_key(item) or ("", ""), []),
            ]
        }
        matches = [group for group in candidates.values() if _matches_group(item, group)]
        if len(matches) == 1:
            group = matches[0]
            group.append(item)
        else:
            group = [item]
            groups.append(group)
        index(item, group)
    bridges = []
    for group in groups:
        workspace = next((item for item in group if item.get("kind") == "worktree"), {})
        evidence = (
            (workspace.get("observation") or sources.get("worktrees") or {})
            if workspace
            else next(
                (
                    item["worktree_observation"]
                    for item in group
                    if item.get("worktree_observation")
                ),
                {},
            )
        )
        owner_current = evidence.get("observation_valid") is True or (
            evidence.get("freshness") == "fresh" and evidence.get("completeness") == "complete"
        )
        row = _project(group, owner_current=owner_current)
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
    from .bridge_observations import local_machine, normalize_targets
    from .routes.sessions import _session_info

    catalog = await target_inventory.inventory(
        request,
        refresh=False,
        **({"local_only": True} if local_only else {}),
    )
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
    host = local_machine(resolver)
    targets = normalize_targets(targets, resolver, catalog["sources"].get("worktrees"))
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
            "handle": row["registered_session_id"] or row["managed_session_id"] or row["handle"],
            "kind": "live"
            if row["registered_session_id"]
            else ("managed" if row["managed_session_id"] else row["location_kind"]),
        }
    return report
