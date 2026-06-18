"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__
from .agent_registry import build_resolver
from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import acp_ws, admin, agents, health, providers, sessions, ui, worktrees
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

    mgr = SessionManager(
        db,
        context_thresholds=cfg.context_thresholds,
        timeouts=cfg.timeouts,
        retention=cfg.retention,
    )
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

    # Start credential relay server for auth forwarding over SSH tunnels and
    # container connections. agent-bridge owns/runs the relay; provider plugins
    # inject their per-target source profiles (see register_credential_sources).
    relay_server = None
    try:
        from credential_relay import RelayBuilder

        from .agent_registry import register_credential_sources

        builder = RelayBuilder()
        register_credential_sources(builder)

        if not builder.empty:
            relay_server = builder.build()
            await relay_server.start()
            app.state.credential_relay = relay_server
            log.info(
                "Credential relay started on port %d (%d sources)",
                relay_server.port,
                len(builder.sources),
            )
        else:
            log.debug("No credential-relay sources registered -- relay disabled")
    except ImportError:
        log.debug("credential-relay lib not installed -- credential relay disabled")
    except OSError as exc:
        log.warning("Credential relay failed to start: %s", exc)

    log.info(
        "agent-bridge started (port=%s, db=%s, sessions=%d)",
        cfg.port, db_path, len(mgr.list_sessions()),
    )

    # Periodic GC sweep -- prune aged terminal/disconnected sessions and
    # compact the DB while the daemon runs (startup GC already ran in the
    # SessionManager constructor). 0 disables.
    gc_task = None
    sweep_hours = cfg.retention.sweep_interval_hours
    if cfg.retention.enabled and sweep_hours and sweep_hours > 0:
        async def _gc_loop() -> None:
            interval = sweep_hours * 3600.0
            while True:
                await asyncio.sleep(interval)
                try:
                    await asyncio.to_thread(mgr.gc, reason="sweep")
                except Exception:
                    log.warning("Periodic GC sweep failed", exc_info=True)

        gc_task = asyncio.create_task(_gc_loop())
        log.info("Periodic GC sweep every %.1fh", sweep_hours)

    yield

    # Shutdown: stop the periodic GC sweep
    if gc_task is not None:
        gc_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await gc_task

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
    # Stash the token so the websocket transport (which bypasses
    # BearerAuthMiddleware) can authenticate connections itself.
    app.state.auth_token = auth_token

    # Auth middleware
    app.add_middleware(BearerAuthMiddleware, token=auth_token)

    # Routes
    app.include_router(health.router)
    app.include_router(ui.router)
    app.include_router(acp_ws.router)
    app.include_router(sessions.router)
    app.include_router(agents.router)
    app.include_router(providers.router)
    app.include_router(worktrees.router)
    app.include_router(admin.router)

    return app
