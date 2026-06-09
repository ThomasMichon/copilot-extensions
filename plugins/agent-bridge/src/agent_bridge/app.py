"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__
from .agent_registry import build_resolver
from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import agents, health, providers, sessions, worktrees
from .session_manager import SessionManager
from .transport import shutdown_ssh

log = logging.getLogger("agent-bridge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan -- initialize DB, topology, and session manager."""
    cfg = app.state.config

    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    app.state.db = db

    mgr = SessionManager(db, context_thresholds=cfg.context_thresholds)
    app.state.session_manager = mgr

    # Load topology profiles + auto-discover local agents
    resolver = build_resolver(cfg)
    app.state.resolver = resolver

    # Start worktree discovery if topology is available
    if resolver:
        from .routes.worktrees import get_cache
        wt_cache = get_cache()
        wt_cache.configure(interval=cfg.worktree_discovery_interval)
        wt_cache.start(resolver)

    # Start credential relay server for auth forwarding over SSH tunnels.
    # Uses agent-codespaces' relay (GitCredentialSource proxies to local GCM).
    relay_server = None
    try:
        from agent_codespaces.credential_relay import CredentialRelayServer
        from agent_codespaces.credential_relay.sources.git_credential import (
            GitCredentialSource,
        )

        relay_server = CredentialRelayServer(
            sources=[GitCredentialSource()],
        )
        await relay_server.start()
        app.state.credential_relay = relay_server
        log.info("Credential relay started on port %d", relay_server.port)
    except ImportError:
        log.debug("agent-codespaces not installed -- credential relay disabled")
    except OSError as exc:
        log.warning("Credential relay failed to start: %s", exc)

    log.info(
        "agent-bridge started (port=%s, db=%s, sessions=%d)",
        cfg.port, db_path, len(mgr.list_sessions()),
    )
    yield

    # Shutdown: stop credential relay
    if relay_server and relay_server.running:
        await relay_server.stop()
        log.info("Credential relay stopped")

    # Shutdown: stop worktree discovery
    from .routes.worktrees import get_cache
    await get_cache().stop()

    # Shutdown: stop all active sessions gracefully
    for session in mgr.list_sessions():
        if session.client and session.client.is_running:
            try:
                log.info("Stopping session %s on shutdown", session.session_id)
                await mgr.stop_session(session.session_id)
            except Exception:
                log.warning(
                    "Failed to stop session %s on shutdown",
                    session.session_id, exc_info=True,
                )

    # Shutdown: disconnect SSH master connections (after sessions are stopped)
    await shutdown_ssh()


def create_app(*, config=None, token: str | None = None) -> FastAPI:
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
    app.include_router(providers.router)
    app.include_router(worktrees.router)

    return app
