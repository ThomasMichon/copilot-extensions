"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__
from .agent_registry import build_resolver
from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import (
    acp_ws,
    admin,
    agents,
    health,
    live_sessions,
    providers,
    sessions,
    ui,
    worktrees,
)
from .session_manager import session_manager_from_config
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


async def _start_credential_relay(app: FastAPI):
    """Build and start the in-process credential relay; return the server or None.

    Extracted so both lifespan startup and the post-cutover relay-adopt endpoint
    can (re)bind the shared relay port (9857). Idempotent: if a relay is already
    running on this app it is returned unchanged.
    """
    existing = getattr(app.state, "credential_relay", None)
    if existing is not None and getattr(existing, "running", False):
        return existing
    relay_server = None
    try:
        from credential_relay import RelayBuilder

        from .agent_registry import register_credential_sources

        builder = RelayBuilder()
        register_credential_sources(builder)
        if builder.empty:
            log.debug("No credential-relay sources registered -- relay disabled")
            return None
        relay_server = builder.build()
        await relay_server.start()
        app.state.credential_relay = relay_server
        log.info(
            "Credential relay started on port %d (%d sources)",
            relay_server.port, len(builder.sources),
        )
        return relay_server
    except ImportError:
        log.debug("credential-relay lib not installed -- credential relay disabled")
    except OSError as exc:
        # #123: a relay bind failure breaks ALL CodeSpace git/ADO auth over the
        # SSH tunnel, so surface it LOUDLY (error, not a quiet warning) with a
        # recovery hint. The single-instance guard now refuses duplicate daemons
        # before they bind, so reaching here is a genuine, unexpected port
        # conflict (a stray non-daemon occupant) worth an operator's attention.
        port = getattr(relay_server, "port", None) or "9857"
        log.error(
            "Credential relay FAILED to bind (port %s): %s -- CodeSpace git/ADO "
            "auth over the tunnel will NOT work until this is resolved. Check "
            "for a stray process holding the relay port and restart the daemon "
            "(agent-bridge service restart).",
            port, exc,
        )
        print(
            f"[agent-bridge] ERROR: credential relay failed to bind on port "
            f"{port}: {exc} -- CodeSpace auth will be broken until resolved.",
            file=sys.stderr,
        )
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan -- initialize DB, topology, and session manager."""
    cfg = app.state.config

    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    db.start_writer()
    app.state.db = db

    mgr = session_manager_from_config(db, cfg)
    app.state.session_manager = mgr

    # Session-Host mode: reattach to any Session Hosts that survived a prior
    # frontend restart (goal 3), instead of leaving those sessions STOPPED.
    # Best-effort: a reattach failure must never block daemon startup.
    if cfg.session_host_enabled:
        try:
            n = await mgr.reattach_session_hosts()
            if n:
                logging.getLogger("agent-bridge").info(
                    "Reattached %d session(s) to surviving Session Hosts", n
                )
        except Exception:
            logging.getLogger("agent-bridge").warning(
                "Session-Host reattach on startup failed", exc_info=True
            )

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
        relay_server = await _start_credential_relay(app)

    # Expose a relay-adoption hook so a passive cutover instance can bind the
    # shared relay port *after* the retiring daemon releases it (the relay is a
    # singleton on 9857). The /api/v1/relay/adopt endpoint calls this.
    async def _adopt_relay():
        nonlocal relay_server
        relay_server = await _start_credential_relay(app)
        return relay_server is not None and getattr(relay_server, "running", False)

    app.state.adopt_relay = _adopt_relay

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

    # Liveness heartbeat (#145) -- periodically confirm each RUNNING session's
    # transport is alive (stamps last_heartbeat_at). A frozen heartbeat then
    # means the channel died (tunnel drop / host sleep); a fresh heartbeat with a
    # stale last_output_at means the agent stalled while the channel is up. This
    # is what lets `sessions`/`status` report a real liveness signal instead of
    # the misleading turn-boundary `updated_at`. Cheap; always on.
    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(15.0)
            try:
                mgr.note_heartbeats()
            except Exception:
                log.warning("Liveness heartbeat beat failed", exc_info=True)
            # Liveness-driven reattach (P1): the beat above only *detects* a
            # dropped transport (a host-backed session reading `disconnected`
            # while its Session Host + child survive); this *acts* on it,
            # redialing the host and resuming by cursor with no restart and no
            # lost turn. No-op unless Session-Host mode is enabled.
            if mgr.session_host_enabled:
                try:
                    await mgr.recover_disconnected_hosts()
                except Exception:
                    log.warning("Liveness-driven reattach failed", exc_info=True)
            # Eventual-terminal reconciliation (#2384): heal any session wedged
            # in RUNNING with no live turn (output stopped, no prompt task) so it
            # cannot mirror "Responding..." forever. Runs regardless of host mode;
            # it never touches a progressing or locally-driven turn.
            try:
                await mgr.reconcile_wedged_running()
            except Exception:
                log.warning("Wedged-session reconciliation failed", exc_info=True)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

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

    # Version-mux sprawl sweep (Phase 4, #1765) -- periodically reap stranded
    # incompatible Session Hosts once their child stops (or they outlive the
    # configured age bound), so an old host-layer generation cannot pin an old
    # on-disk install for a whole frontend lifetime. Only runs in Session-Host
    # mode; harmless (empty) unless a breaking host-protocol change left older
    # hosts running.
    host_sweep_task = None
    if cfg.session_host_enabled:
        async def _host_sweep_loop() -> None:
            # A few times per bound (min 60s) when a bound is set; otherwise an
            # hourly cadence just to reap children that reached their own stop.
            bound = cfg.session_host_stale_reap_seconds
            interval = max(60.0, bound / 4) if bound and bound > 0 else 3600.0
            while True:
                await asyncio.sleep(interval)
                try:
                    n = await asyncio.to_thread(mgr.sweep_stranded_hosts)
                    if n:
                        log.info("Version-mux sweep reaped %d stranded host(s)", n)
                except Exception:
                    log.warning("Version-mux stranded-host sweep failed", exc_info=True)

        host_sweep_task = asyncio.create_task(_host_sweep_loop())

    # Idle-session reaper (#1826, ownership inversion) -- the bridge owns
    # session process lifetime by connection + state, so a front need only
    # connect/disconnect. Periodically stop idle, unwatched sessions past the
    # TTL, freeing their Copilot children (resumable via replay). Only runs in
    # Session-Host mode with a positive TTL configured.
    idle_reap_task = None
    if cfg.session_host_enabled and cfg.idle_reap_ttl_seconds > 0:
        async def _idle_reap_loop() -> None:
            interval = max(30.0, float(cfg.idle_reap_sweep_seconds or 300))
            while True:
                await asyncio.sleep(interval)
                try:
                    n = await mgr.sweep_idle_sessions()
                    if n:
                        log.info(
                            "Idle-reaper stopped %d idle unwatched session(s)", n
                        )
                except Exception:
                    log.warning("Idle-session sweep failed", exc_info=True)

        idle_reap_task = asyncio.create_task(_idle_reap_loop())
        log.info(
            "Idle-session reaper armed: TTL=%ds, sweep every %ds",
            cfg.idle_reap_ttl_seconds,
            max(30, cfg.idle_reap_sweep_seconds or 300),
        )

    # Live-session lease reaper (#2880/#2906) -- expire live-CLI registrations
    # whose heartbeat lease has lapsed (a Copilot CLI that exited without a
    # clean deregister stops re-POSTing) so a dead CLI cannot leave a worktree
    # permanently un-ownable or accept a racing steer, and drop inbox messages
    # that can never be delivered to that incarnation. Cheap; always on. Sweeps
    # at half the lease window so a lapsed row is demoted within ~one window.
    from .db import LIVE_SESSION_STALE_SECONDS

    async def _live_reap_loop() -> None:
        interval = max(30.0, LIVE_SESSION_STALE_SECONDS / 2)
        while True:
            await asyncio.sleep(interval)
            try:
                n = await asyncio.to_thread(
                    db.reap_stale_live_sessions, now=time.time()
                )
                if n:
                    log.info(
                        "Live-session reaper expired %d stale registration(s)", n
                    )
            except Exception:
                log.warning("Live-session lease reap failed", exc_info=True)

    live_reap_task = asyncio.create_task(_live_reap_loop())

    # Routing-table publish-on-ready -- a normal daemon announces its endpoint
    # to active.json once it is actually listening, so CLI clients discover it
    # via the routing table. A passive cutover instance leaves publish_on_ready
    # False; the deploy orchestrator flips the table after its own health check.
    publish_task = None
    if getattr(app.state, "publish_on_ready", False):
        async def _publish_when_listening() -> None:
            import os as _os

            from . import __version__ as _ver
            from zdd import routing
            from .config import config_dir

            server = getattr(app.state, "uvicorn_server", None)
            # Wait for uvicorn to actually bind the socket before announcing.
            for _ in range(600):  # ~60s ceiling
                if server is not None and getattr(server, "started", False):
                    break
                await asyncio.sleep(0.1)
            try:
                await asyncio.to_thread(
                    routing.publish_active,
                    config_dir(),
                    bind=cfg.bind,
                    port=cfg.port,
                    pid=_os.getpid(),
                    version=_ver,
                    demote_existing=True,
                )
            except Exception:
                log.warning("Failed to publish routing table", exc_info=True)

        publish_task = asyncio.create_task(_publish_when_listening())

    yield

    # Shutdown: retract our routing-table claim so clients fall back (or follow
    # a successor that already flipped the table). Done first so no new client
    # is routed to us while we tear sessions down.
    if publish_task is not None:
        publish_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publish_task
    if getattr(app.state, "publish_on_ready", False):
        import os as _os

        from zdd import routing
        from .config import config_dir
        try:
            await asyncio.to_thread(routing.clear_if_owner, config_dir(), _os.getpid())
        except Exception:
            log.debug("Routing-table clear-on-shutdown skipped", exc_info=True)

    # Shutdown: stop the idle-shutdown monitor
    if idle_task is not None:
        idle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await idle_task

    # Shutdown: stop the liveness heartbeat (#145)
    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task

    # Shutdown: stop the periodic GC sweep
    if gc_task is not None:
        gc_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await gc_task

    # Shutdown: stop the version-mux stranded-host sweep
    if host_sweep_task is not None:
        host_sweep_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await host_sweep_task

    # Shutdown: stop the idle-session reaper (#1826)
    if idle_reap_task is not None:
        idle_reap_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await idle_reap_task

    # Shutdown: stop the live-session lease reaper (#2880/#2906)
    live_reap_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await live_reap_task

    # Shutdown: stop credential relay
    if relay_server and relay_server.running:
        await relay_server.stop()
        log.info("Credential relay stopped")

    # Shutdown: stop worktree discovery
    from .routes.worktrees import get_cache
    await get_cache().stop()

    # Shutdown: Session-Host mode -- assertively-but-nicely cancel in-flight
    # turns (ACP session/cancel + a resume_on_reattach flag) before tearing the
    # sessions down, so a bare `systemctl restart` (no installer drain) is fast
    # and clean, and mid-turn sessions get a "Resume" once the new frontend
    # reattaches. No-op unless session_host_enabled.
    if cfg.session_host_enabled:
        try:
            await mgr.graceful_cancel_for_redeploy()
        except Exception:
            log.warning("Graceful-cancel on shutdown failed", exc_info=True)

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
    # In-memory registry of represented live-session event logs (Phase 5). Kept
    # off the ACP-owned SessionManager; see live_representation for rationale.
    from .live_representation import LiveEventStore
    app.state.live_event_store = LiveEventStore()

    # Auth middleware
    app.add_middleware(BearerAuthMiddleware, token=auth_token)

    # Routes
    app.include_router(health.router)
    app.include_router(ui.router)
    app.include_router(acp_ws.router)
    app.include_router(sessions.router)
    app.include_router(live_sessions.router)
    app.include_router(agents.router)
    app.include_router(providers.router)
    app.include_router(worktrees.router)
    app.include_router(admin.router)

    return app
