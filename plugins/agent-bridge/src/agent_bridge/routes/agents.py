"""Agent registry endpoints -- /api/v1/agents/*."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("")
async def list_agents():
    """List registered agent profiles.

    Phase 1: returns empty list. Agent registry integration is Phase 2
    when topology profiles and machines.yaml parsing are added.
    """
    return {"agents": []}
