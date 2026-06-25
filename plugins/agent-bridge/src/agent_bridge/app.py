"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
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

# Session statuses that mean "a host is actively using this daemon". When none
# of these are present, the idle-shutdown monitor (if armed) counts down.
_ACTIVE_STATUSES = {"created", "starting", "running", "idle"}


def _count_active_sessions(mgr) -> int:
    """Count sessions that are live (not a terminal/stopped state)."""
    n = 0
    for s in mgr.list_sessions():
        st = getattr(s, "status", None)
        st = getattr(st, "value", st)
        if str(st).lower() in _ACTIVE_STATUSES:
            n += 1
    return n


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan -- initialize DB, topology, and session manager."""
    cfg = app.state.config

    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    db.start_writer()
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
    # The elevated sub-daemon disables this (enable_credential_relay=False) so it
    # never re-binds -- and thus never evicts -- the primary daemon's relay on the
    # shared loopback port 9857; local elevated agents reuse the primary's relay.
    relay_server = None
    if not getattr(cfg, "enable_credential_relay", True):
        log.info(
            "Credential relay disabled for this daemon "
            "(enable_credential_relay=False) -- reusing the primary daemon's relay"
        )
    else:
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

    # Idle auto-shutdown -- the elevated sub-daemon (and any caller that passes
    # idle_shutdown_seconds) exits once no host needs it, so it does not linger.
    # The primary daemon leaves this at 0 and stays up indefinitely.
    idle_task = None
    idle_secs = getattr(cfg, "idle_shutdown_seconds", 0) or 0
    if idle_secs > 0:
        async def _idle_loop() -> None:
            poll = max(5.0, min(30.0, idle_secs / 4))
            last_active = time.monotonic()
            while True:
                await asyncio.sleep(poll)
                try:
                    active = await asyncio.to_thread(_count_active_sessions, mgr)
                except Exception:
                    log.warning("Idle-shutdown check failed", exc_info=True)
                    continue
                if active > 0:
                    last_active = time.monotonic()
                    continue
                idle_for = time.monotonic() - last_active
                if idle_for >= idle_secs:
                    log.info(
                        "Idle %.0fs with no active sessions -- shutting down",
                        idle_for,
                    )
                    server = getattr(app.state, "uvicorn_server", None)
                    if server is not None:
                        server.should_exit = True
                    return

        idle_task = asyncio.create_task(_idle_loop())
        log.info(
            "Idle-shutdown armed: exit after %ds with no active sessions",
            idle_secs,
        )

    yield

    # Shutdown: stop the idle-shutdown monitor
    if idle_task is not None:
        idle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await idle_task

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

    # Shutdown: persist every queued event before the process exits.
    try:
        await asyncio.to_thread(db.close)
    except Exception:
        log.warning("Failed to stop event writer cleanly", exc_info=True)


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
