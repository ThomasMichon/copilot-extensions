"""Provider endpoints -- /api/v1/providers/*.

External agent providers (e.g. agent-codespaces) register dynamic agents
via these endpoints. Provider agents are merged into the resolver with
lowest precedence -- static and auto-discovered agents always win on
name conflicts.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("agent-bridge")

router = APIRouter(tags=["providers"])

# Allowed provider and agent name pattern
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ProviderAgent(BaseModel):
    """Agent definition from a provider registration request.

    Only fields relevant for provider agents are accepted -- arbitrary
    env, setup_script, managed, etc. are not allowed from external
    providers.
    """

    name: str = Field(..., description="Agent name (must be unique)")
    display_name: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Agent description")
    icon: str | None = Field(None, description="Icon identifier")
    spawn_command: list[str] = Field(
        ..., description="Raw command to spawn this agent (ACP on stdio)",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_PATTERN.match(v):
            raise ValueError(
                f"Agent name must match {_NAME_PATTERN.pattern}, got '{v}'"
            )
        return v

    @field_validator("spawn_command")
    @classmethod
    def _validate_spawn_command(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("spawn_command must not be empty")
        return v


class RegisterProviderRequest(BaseModel):
    """Request body for provider registration."""

    agents: list[ProviderAgent] = Field(
        ..., description="Agents contributed by this provider",
    )
    ttl: float = Field(
        300.0,
        description="Seconds before agents expire (0 = no expiry)",
        ge=0,
        le=86400,
    )


@router.post("/api/v1/providers/{provider_name}")
async def register_provider(
    provider_name: str,
    body: RegisterProviderRequest,
    request: Request,
) -> dict[str, Any]:
    """Register or refresh an agent provider."""
    if not _NAME_PATTERN.match(provider_name):
        raise HTTPException(
            status_code=400,
            detail=f"Provider name must match {_NAME_PATTERN.pattern}",
        )

    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        raise HTTPException(
            status_code=503,
            detail="Resolver not initialized -- no topology loaded",
        )

    from ..agent_registry import AgentConfig

    agents: dict[str, AgentConfig] = {}
    for agent in body.agents:
        agents[agent.name] = AgentConfig(
            name=agent.name,
            display_name=agent.display_name,
            description=agent.description,
            icon=agent.icon,
            spawn_command=agent.spawn_command,
            provider=provider_name,
        )

    provider = resolver.register_provider(
        provider_name, agents, ttl=body.ttl,
    )

    return {
        "status": "registered",
        "provider": provider_name,
        "agents": len(provider.agents),
        "ttl": provider.ttl,
    }


@router.delete("/api/v1/providers/{provider_name}")
async def unregister_provider(
    provider_name: str,
    request: Request,
) -> dict[str, Any]:
    """Unregister a provider and remove its agents."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not resolver.unregister_provider(provider_name):
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider_name}' not found",
        )

    return {"status": "unregistered", "provider": provider_name}


@router.get("/api/v1/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    """List registered providers with status."""
    resolver = getattr(request.app.state, "resolver", None)
    if not resolver:
        return {"providers": []}

    return {"providers": resolver.list_providers()}
