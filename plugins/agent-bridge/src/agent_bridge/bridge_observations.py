"""Passive identity and cache adapters for the conversation inventory."""

from __future__ import annotations

from typing import Any


def local_machine(resolver: Any) -> str | None:
    value = getattr(getattr(resolver, "_local_machine", None), "key", None)
    return value if isinstance(value, str) and value else None


def normalize_targets(targets: list[dict[str, Any]], resolver: Any) -> list[dict[str, Any]]:
    """Keep transport addresses separate from configured execution identities."""
    host = local_machine(resolver)
    aliases: dict[str, str | None] = {}
    normalized = []
    for item in targets:
        field = "session" if item.get("kind") == "managed" else "registration"
        row = item.get(field) or {}
        machine = row.get("machine")
        if item.get("kind") == "managed":
            address = row.get("target_host")
            if row.get("target_type") == "local":
                machine = machine or host
            elif row.get("target_type") == "ssh" and isinstance(address, str):
                if address not in aliases:
                    resolve = getattr(resolver, "resolve_ssh_environment", None)
                    try:
                        owner, _environment = (
                            resolve(address) if callable(resolve) else (None, None)
                        )
                        key = getattr(owner, "key", None)
                        aliases[address] = key if isinstance(key, str) and key else None
                    except ValueError:
                        aliases[address] = None
                machine = machine or aliases[address]
        elif item.get("kind") == "session_host" and row.get("boundary") == "local":
            machine = machine or host
        normalized.append({**item, field: {**row, "machine": machine}} if row else item)
    return normalized


def cached_local_worktrees(request: Any) -> dict[str, Any]:
    """Never start or join the global cache's remote-source crawl."""
    from .routes import worktrees
    from .routes.worktree_refresh import worktree_sources
    from .local_session_catalog import REPOSITORY_INVENTORY_UNAVAILABLE

    resolver = getattr(request.app.state, "resolver", None)
    host = local_machine(resolver)
    if host is None:
        return {
            "groups": {},
            "discovery": {
                "version": 1,
                "freshness": "unknown",
                "completeness": "incomplete",
                "cache_only": True,
                "scope": "local",
                "diagnostic": "local_identity_unknown",
            },
        }
    cache = worktrees.cache_for_app(request.app)
    metadata = cache._refresh.metadata()
    cached = cache.get_all()
    configured = worktree_sources(resolver)
    expected = {
        name
        for name, config in configured.items()
        if config.project and config.worktree_discovery and not config.host and not config.provider
    }
    groups = {
        name: [entry.to_dict() for entry in entries if entry.machine == host]
        for name, entries in cached.items()
        if name in expected or any(entry.machine == host for entry in entries)
    }
    names = expected | set(groups)
    observations = {
        name: metadata.get("sources", {}).get(
            name,
            {
                "freshness": "unknown",
                "completeness": "unknown",
                "diagnostic": "cache_not_observed",
            },
        )
        for name in names
    }
    mismatch = any(entry.machine != host for name in expected for entry in cached.get(name, []))
    catalog_available = not getattr(resolver, "repository_inventory_unavailable", False)
    complete = (
        catalog_available
        and not mismatch
        and all(item.get("completeness") == "complete" for item in observations.values())
    )
    fresh = complete and all(item.get("freshness") == "fresh" for item in observations.values())
    return {
        "groups": groups,
        "discovery": {
            **metadata,
            "sources": observations,
            "cache_only": True,
            "scope": "local",
            "freshness": "fresh" if fresh else "unknown" if not observations else "stale",
            "completeness": "complete" if complete else "incomplete",
            "repository_inventory": {
                "availability": "available" if catalog_available else "unavailable",
                "reason": None if catalog_available else REPOSITORY_INVENTORY_UNAVAILABLE,
            },
            "diagnostic": REPOSITORY_INVENTORY_UNAVAILABLE
            if not catalog_available
            else "execution_identity_mismatch"
            if mismatch
            else (None if fresh else "local_cache_incomplete"),
        },
    }
