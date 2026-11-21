"""Complete a verified local rendering from the original passive inventory owners."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, cast

REPOSITORY_INVENTORY_UNAVAILABLE = "local-repository-inventory-unavailable"


def read_repository_inventory(
    *, prefix: list[str] | None = None, environment: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Read the selected registry owner without invoking a provisioning shim."""
    from .provider_process import run_captured
    from .session_lifecycle_cli import (
        _agent_worktrees_environment, _agent_worktrees_launch_prefix,
    )

    if prefix is None:
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
    if not prefix:
        raise ValueError("worktree_owner_unavailable")
    if environment is None:
        environment = _agent_worktrees_environment()
    listing = run_captured(
        [*prefix, "repos", "list", "--json", "--strict"], timeout=15.0, env=environment,
    )
    if listing.returncode:
        raise ValueError("repository_inventory_unavailable")
    document = json.loads(listing.stdout)
    if not isinstance(document, dict) or document.get("inventory_authority") != "complete":
        raise ValueError("repository_inventory_unqualified")
    repos = document.get("repos")
    if not isinstance(repos, list) or any(not isinstance(item, dict) for item in repos):
        raise ValueError("repository_inventory_malformed")
    return cast(list[dict[str, Any]], repos)


def _locally_bound(client: Any) -> bool:
    if not isinstance(getattr(client, "_base", None), str):
        return False
    if os.environ.get("AGENT_BRIDGE_BASE_URL"):
        return False
    from zdd.routing import read_active_endpoint

    from .config import config_dir
    from .routing_state import forwarded_route_base_url

    try:
        root = config_dir()
        if forwarded_route_base_url(root) is not None:
            return False
        endpoint = read_active_endpoint(root, verify_listener=True)
        return endpoint is not None and endpoint.base_url == client._base
    except (OSError, ValueError):
        return False


def _workspace_key(target: dict[str, Any]) -> tuple[str, str, str, str] | None:
    if target.get("kind") != "worktree":
        return None
    row = target.get("worktree")
    if not isinstance(row, dict):
        return None
    path = row.get("path")
    return (
        str(target.get("project") or ""), str(row.get("machine") or ""),
        str(row.get("id") or ""),
        os.path.normcase(os.path.realpath(path)) if isinstance(path, str) else "",
    )


def _inventory_observation(
    raw: str, *, read_started: float | None = None,
) -> tuple[dict[str, Any], float | None]:
    from .routes.worktree_refresh import DiscoveryReadError, inventory_window

    document = json.loads(raw)
    if isinstance(document, dict):
        if (
            type(document.get("version", 1)) is not int or document.get("version", 1) != 1
            or type(document.get("contract_version", 1)) is not int
            or document.get("contract_version", 1) != 1
        ):
            raise ValueError("incompatible_worktree_inventory")
        # A render may retain unavailable rows, but never malformed row identities.
        inventory_window(json.dumps(document.get("worktrees")))
    else:
        inventory_window(raw)
    try:
        _observed, valid_until = inventory_window(raw, read_started=read_started)
    except DiscoveryReadError as error:
        if str(error) not in {"source-stale", "source-degraded", "source-incomplete"}:
            raise
        return {"availability": "unavailable", "reason": str(error), "observation_valid": False}, None
    return {
        "availability": "available",
        "observation_valid": valid_until is None or time.monotonic() < valid_until,
    }, valid_until


def complete_catalog(client: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    """Read local sources without starting services, registering or hydrating."""
    if not _locally_bound(client):
        return catalog
    from . import agent_registry
    from .agent_registry_topology import local_inventory_sources
    from .provider_process import run_captured
    from .routes.worktree_refresh import DiscoveryReadError
    from .routes.worktrees import _parse_worktree_list
    from .session_host.registered_inventory import registered_hosts
    from .target_inventory import same_host_observation
    from .session_lifecycle_cli import (
        _agent_worktrees_environment, _agent_worktrees_launch_prefix,
    )

    targets = list(catalog["targets"])
    sources = dict(catalog["sources"])
    hosts, sources["local_session_host"] = registered_hosts()
    for host in hosts:
        registration = host["registration"]
        if not any(same_host_observation(registration, item.get("registration")) for item in targets):
            targets.append(host)
    observations: dict[str, dict[str, Any]] = {}
    windows: dict[str, tuple[dict[str, Any], float | None]] = {}
    deadline = time.monotonic() + 30.0
    try:
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
        if not prefix:
            raise ValueError("worktree_owner_unavailable")
        environment = _agent_worktrees_environment()
        repos = read_repository_inventory(prefix=prefix, environment=environment)
        projects = local_inventory_sources(repos, agent_registry._detect_platform(), {})
        known_workspaces = {
            _workspace_key(item): item for item in targets if _workspace_key(item) is not None
        }
        for source, config in projects.items():
            project = config.project
            if not project:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                observations[project] = {"availability": "unavailable", "reason": "read_deadline"}
                continue
            try:
                read_started = time.monotonic()
                result = run_captured(
                    [*prefix, "--project", project, "list", "--json", "--passive"],
                    timeout=remaining, env=environment,
                )
                if result.returncode:
                    raise ValueError("passive_inventory_unavailable")
                observation, valid_until = _inventory_observation(result.stdout, read_started=read_started)
                windows[project] = observation, valid_until
                rows = _parse_worktree_list(result.stdout, source)
                for row in rows:
                    target: dict[str, Any] = {
                        "kind": "worktree", "handle": row.id, "sources": ["local-worktree-owner"],
                        "owner": None, "project": project, "worktree": row.to_dict(),
                        "observation": observation,
                    }
                    workspace_key = _workspace_key(target)
                    if workspace_key not in known_workspaces:
                        targets.append(target)
                        known_workspaces[workspace_key] = target
                    else:
                        existing = known_workspaces[workspace_key]
                        linked = {
                            name: existing["worktree"][name] for name in (
                                "agent_name", "path", "session_id", "session_status", "session_live",
                                "acp_session_id", "durable_session_id", "session_at_rest", "session_turn_count",
                            ) if name in existing["worktree"]
                        }
                        existing["worktree"] = {**target["worktree"], **linked}
                        existing["observation"] = observation
                        existing["sources"] = list(dict.fromkeys([
                            *existing["sources"], "local-worktree-owner",
                        ]))
                observations[project] = {**observation, "count": len(rows)}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                observations[project] = {
                    "availability": "unavailable",
                    "reason": str(error) if isinstance(error, DiscoveryReadError) else type(error).__name__,
                }
        sources["local_worktrees"] = {
            "availability": "available" if all(
                item["availability"] == "available" for item in observations.values()
            ) else "unavailable",
            "projects": observations,
        }
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sources["local_worktrees"] = {
            "availability": "unavailable", "reason": type(error).__name__, "projects": observations,
        }
    now = time.monotonic()
    for project, (observation, valid_until) in windows.items():
        if valid_until is not None and now >= valid_until:
            stale = {"availability": "unavailable", "reason": "source-stale", "observation_valid": False}
            observation.update(stale)
            observations[project].update(stale)
            sources["local_worktrees"]["availability"] = "unavailable"
    return {**catalog, "targets": targets, "sources": sources}
