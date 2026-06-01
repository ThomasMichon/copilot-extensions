"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .agent_registry import build_resolver
from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import agents, health, sessions
from .session_manager import SessionManager

from . import __version__

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

    # Load topology profiles + auto-discover local agents
    resolver = build_resolver(cfg)
    app.state.resolver = resolver

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
        version=__version__,
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
