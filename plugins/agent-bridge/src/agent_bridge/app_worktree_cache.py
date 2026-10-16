"""Lifespan custody for the actual worktree-discovery cache."""

from __future__ import annotations

from fastapi import FastAPI

from .agent_registry_resolver import AgentResolver
from .loop_governance import LoopGovernance
from .models import ServiceConfig
from .routes.worktrees import cache_for_app


def start_worktree_cache(
    app: FastAPI, resolver: AgentResolver, cfg: ServiceConfig, governance: LoopGovernance,
) -> None:
    """Retain the exact configured cache before starting its ordinary owner."""
    if resolver.agents or resolver.machines:
        cache = cache_for_app(app)
        app.state.worktree_discovery_cache = cache
        cache.configure(interval=cfg.worktree_discovery_interval)
        cache.start(resolver, governance=governance)


async def stop_worktree_cache(app: FastAPI) -> None:
    """Stop the retained cache, not whichever singleton a later caller selected."""
    await cache_for_app(app).stop()
