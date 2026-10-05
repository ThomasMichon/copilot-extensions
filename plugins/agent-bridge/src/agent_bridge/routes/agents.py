"""Agent and machine endpoints -- /api/v1/agents/*, /api/v1/machines/*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["agents"])


@router.get("/api/v1/agents")
async def list_agents(
    request: Request,
    force_refresh: bool = False,
    require_complete: bool = False,
):
    """List registered agent profiles.

    ``force_refresh`` and ``require_complete`` are new, protocol-gated
    request parameters (pivot-streaming-transport Phase 3b): additive and
    harmless against an old daemon that doesn't understand them (ignored,
    same old-shape response), but a *new* daemon's own default response
    shape is completely unchanged for a caller that doesn't send them --
    healthy namespaces' rows plus ``incomplete_namespaces`` naming the rest,
    never a ``503``. Only a ``require_complete`` caller can ever receive a
    ``503`` (nothing authoritative to serve yet, or still), and only via
    the daemon-side background cache below -- never a new response field.
    """
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:
        if require_complete:
            raise HTTPException(status_code=503, detail="agent roster not yet available")
        # The pre-3b, byte-for-byte resolver-absent shape -- never widen
        # this to the 4-key payload below; an old client's own assumptions
        # about this exact degenerate shape must keep holding.
        return {"agents": []}

    topology_ready = getattr(request.app.state, "topology_ready", True)
    if not topology_ready:
        if require_complete:
            raise HTTPException(status_code=503, detail="agent roster not yet available")
        list_async = getattr(resolver, "list_agents_async", None)
        agents = await list_async() if callable(list_async) else []
        return {
            "agents": agents,
            "topology_errors": getattr(resolver, "topology_errors", []),
            "topology_warnings": getattr(resolver, "topology_warnings", []),
            "incomplete_namespaces": getattr(resolver, "incomplete_namespaces", []),
        }

    cache = getattr(request.app.state, "agent_roster_cache", None)
    cache_usable = cache is not None and getattr(cache, "resolver", None) is resolver
    if not cache_usable:
        if require_complete:
            # This daemon generation DOES advertise the capability (the
            # client only sends `require_complete` after confirming that via
            # `daemon_supports()`) -- a transient internal race leaving no
            # usable cache bound to the *current* resolver (e.g. mid
            # background-readiness retry, between swapping in a new
            # resolver and (re)installing its cache) must still fail closed,
            # never silently degrade to serving a possibly-stale/partial
            # roster as if it were a pre-3b daemon that never promised
            # completeness in the first place.
            raise HTTPException(status_code=503, detail="agent roster not yet available")
        # No background cache wired up for this resolver (an older startup
        # path, or a test driving the resolver directly), or a stale cache
        # left bound to a resolver this request's `app.state.resolver` no
        # longer is -- fall back to the pre-3b per-call scan rather than
        # silently serving a different resolver's cached rows. Safe only
        # because the caller never asked for the fail-closed contract above.
        list_async = getattr(resolver, "list_agents_async", None)
        agents = await list_async() if callable(list_async) else []
        return {
            "agents": agents,
            "topology_errors": getattr(resolver, "topology_errors", []),
            "topology_warnings": getattr(resolver, "topology_warnings", []),
            "incomplete_namespaces": getattr(resolver, "incomplete_namespaces", []),
        }

    snapshot = await cache.get_snapshot(force_refresh=force_refresh)
    if require_complete and not snapshot.complete:
        raise HTTPException(status_code=503, detail="agent roster incomplete")
    return {
        "agents": snapshot.rows,
        "topology_errors": getattr(resolver, "topology_errors", []),
        "topology_warnings": getattr(resolver, "topology_warnings", []),
        "incomplete_namespaces": snapshot.incomplete_namespaces,
    }


@router.get("/api/v1/agents/{agent_name}")
async def get_agent(
    agent_name: str,
    request: Request,
    include_unaddressable: bool = False,
):
    """Get agent profile detail."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    lookup = getattr(resolver, "get_agent_config", None)
    config = (
        lookup(agent_name)
        if callable(lookup)
        else getattr(resolver, "agents", {}).get(agent_name)
    )
    if not config:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    if not include_unaddressable and not getattr(config, "spawnable_as_target", True):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    return {
        "name": config.name,
        "display_name": config.display_name or config.name,
        "aliases": list(config.aliases),
        "description": config.description or "",
        "icon": config.icon,
        "managed": config.managed,
        "spawnable_as_target": getattr(config, "spawnable_as_target", True),
        "spawnable": (not config.managed) and getattr(config, "spawnable_as_target", True),
        "target_type": (
            "local"
            if (not config.host or resolver._is_local_loopback_agent(config))
            else "ssh"
        ),
        "host": config.host or "",
        "machine_key": (
            resolver.machine_key_for_agent(config)
            if callable(getattr(resolver, "machine_key_for_agent", None))
            else None
        ),
        "ssh_user": config.ssh_user,
        "ssh_environment": config.ssh_environment,
        "cwd": config.cwd,
        "copilot_path": config.copilot_path,
        "copilot_args": config.copilot_args,
        "worktree_root": config.worktree_root,
        "env": config.env or {},
        "project": config.project,
        "auto_discovered": config.auto_discovered,
    }


@router.get("/api/v1/machines")
async def list_machines(request: Request):
    """List machines from loaded topology."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        return {"machines": []}

    machines = []
    for mc in resolver.machines.values():
        machines.append({
            "key": mc.key,
            "display_name": mc.display_name,
            "environment": mc.environment,
            "role": mc.role,
            "description": mc.description,
            "capabilities": list(mc.capabilities),
            "field_terminal": mc.field_terminal,
            "ssh_ready": mc.ssh_ready,
            "ssh_environments": [
                {
                    "name": e.name,
                    "alias": e.alias,
                    "port": e.port,
                    "user": e.user,
                    "shell": e.shell,
                }
                for e in mc.ssh_environments
            ],
        })
    errors = getattr(resolver, "topology_errors", [])
    warnings = getattr(resolver, "topology_warnings", [])
    return {
        "machines": machines,
        "topology_errors": errors if isinstance(errors, list) else [],
        "topology_warnings": warnings if isinstance(warnings, list) else [],
    }


@router.get("/api/v1/machines/{machine_key}")
async def get_machine(machine_key: str, request: Request):
    """Get machine detail with SSH environments."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_key}' not found")

    mc = resolver.machines.get(machine_key)
    if not mc:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_key}' not found")

    return {
        "key": mc.key,
        "display_name": mc.display_name,
        "environment": mc.environment,
        "role": mc.role,
        "description": mc.description,
        "capabilities": list(mc.capabilities),
        "field_terminal": mc.field_terminal,
        "ssh_ready": mc.ssh_ready,
        "ssh_ip": mc.ssh_ip,
        "ssh_environments": [
            {
                "name": e.name,
                "alias": e.alias,
                "port": e.port,
                "user": e.user,
                "shell": e.shell,
            }
            for e in mc.ssh_environments
        ],
    }
