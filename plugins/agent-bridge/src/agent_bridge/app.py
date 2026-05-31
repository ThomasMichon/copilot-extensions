"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import agents, health, sessions
from .session_manager import SessionManager

log = logging.getLogger("agent-bridge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan -- initialize DB and session manager on startup."""
    cfg = app.state.config

    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    app.state.db = db

    mgr = SessionManager(db)
    app.state.session_manager = mgr

    log.info(
        "agent-bridge started (port=%s, db=%s, sessions=%d)",
        cfg.port, db_path, len(mgr.list_sessions()),
    )
    yield

    # Shutdown: stop all active sessions gracefully
    for session in mgr.list_sessions():
        if session.process and session.process.alive:
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
