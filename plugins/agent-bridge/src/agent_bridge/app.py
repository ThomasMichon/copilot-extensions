"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .agent_registry import AgentResolver, load_agent_registry
from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import agents, health, sessions
from .session_manager import SessionManager
from .topology import load_machines_yaml

log = logging.getLogger("agent-bridge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan -- initialize DB, topology, and session manager."""
    cfg = app.state.config

    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    app.state.db = db

    mgr = SessionManager(db)
    app.state.session_manager = mgr

    # Load topology profiles and build resolver
    all_machines = {}
    all_agents = {}
    for profile_name, profile in cfg.topologies.items():
        if profile.machines_yaml:
            machines = load_machines_yaml(profile.machines_yaml)
            all_machines.update(machines)
        if profile.agents_config:
            agents_cfg = load_agent_registry(profile.agents_config)
            all_agents.update(agents_cfg)

    if all_machines or all_agents:
        resolver = AgentResolver(all_agents, all_machines)
        app.state.resolver = resolver
        log.info(
            "Loaded topology: %d machines, %d agents",
            len(all_machines), len(all_agents),
        )
    else:
        app.state.resolver = None
        log.info("No topology profiles configured")

    log.info(
        "agent-bridge started (port=%s, db=%s, sessions=%d)",
        cfg.port, db_path, len(mgr.list_sessions()),
    )
    yield

    # Shutdown: stop all active sessions gracefully
    for session in mgr.list_sessions():
        if session.client and session.client.is_running:
            log.info("Stopping session %s on shutdown", session.session_id)
            await mgr.stop_session(session.session_id)


def create_app(*, config=None, token: str | None = None) -> FastAPI:  # noqa: ANN001
    """Create and configure the FastAPI application."""
    cfg = config or load_config()
    auth_token = token or load_or_create_auth_token()

    app = FastAPI(
        title="Agent Bridge",
        description="Persistent inter-agent communication service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.config = cfg

    # Auth middleware
    app.add_middleware(BearerAuthMiddleware, token=auth_token)

    # Routes
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(agents.router)

    return app
