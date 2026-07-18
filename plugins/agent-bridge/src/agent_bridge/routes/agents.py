"""Agent and machine endpoints -- /api/v1/agents/*, /api/v1/machines/*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["agents"])


@router.get("/api/v1/agents")
async def list_agents(request: Request):
    """List registered agent profiles."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        return {"agents": []}
    return {"agents": await resolver.list_agents_async()}


@router.get("/api/v1/agents/{agent_name}")
async def get_agent(agent_name: str, request: Request):
    """Get agent profile detail."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    config = resolver.agents.get(agent_name)
    if not config:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    return {
        "name": config.name,
        "display_name": config.display_name or config.name,
        "description": config.description or "",
        "icon": config.icon,
        "managed": config.managed,
        "spawnable": not config.managed,
        "target_type": (
            "local"
            if (not config.host or resolver._is_local_loopback_agent(config))
            else "ssh"
        ),
        "host": config.host or "",
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
    return {"machines": machines}


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
