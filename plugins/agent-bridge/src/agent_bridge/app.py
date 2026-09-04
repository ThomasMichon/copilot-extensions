"""FastAPI application -- lifespan, middleware, route registration."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__, telemetry
from .agent_registry import AgentResolver, daemon_resolver
from .auth import BearerAuthMiddleware
from .config import load_config, load_or_create_auth_token
from .db import Database
from .routes import (
    acp_ws,
    admin,
    agents,
    health,
    live_sessions,
    remote,
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


def _count_active_sessions(mgr, db=None) -> int:
    """Count work this daemon still owns or represents.

    Supersession self-retire uses this as its idleness gate. Count both
    manager-owned ACP sessions and still-live Session Hosts, plus fresh live-CLI
    registrations when a database is available. Stale live registrations are
    deliberately ignored; the live-session reaper reconciles those separately.
    """
    n = 0
    for s in mgr.list_sessions():
        st = getattr(s, "status", None)
        st = getattr(st, "value", st)
        if str(st).lower() in _ACTIVE_STATUSES:
            n += 1
    live_hosts = getattr(mgr, "_live_host_records", None)
    if callable(live_hosts):
        try:
            n += len(live_hosts())
        except Exception:
            log.debug("Self-retire host-record count failed", exc_info=True)
    if db is not None:
        list_fresh = getattr(db, "list_fresh_live_sessions", None)
        if callable(list_fresh):
            try:
                n += len(list_fresh(now=time.time()))
            except Exception:
                log.debug(
                    "Self-retire live-session registration count failed",
                    exc_info=True,
                )
    return n


async def _run_in_daemon_thread(fn, *args):
    """Await blocking readiness work without letting it pin process shutdown."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(setter, value) -> None:
        if not future.done():
            setter(value)

    def run() -> None:
        try:
            result = fn(*args)
        except BaseException as exc:
            try:
                loop.call_soon_threadsafe(deliver, future.set_exception, exc)
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(deliver, future.set_result, result)
            except RuntimeError:
                pass

    threading.Thread(target=run, daemon=True, name="bridge-readiness").start()
    return await future


# Generation self-retire tuning. Default-ON (opt-out): validated on real cutovers
# (it arms for cutover-promoted daemons and self-retires a demoted generation
# without disturbing in-flight work), so it is now the default. Set
# ``AGENT_BRIDGE_SELF_RETIRE=0`` (or false/no/off) to disable it. Cadence and the
# K-confirmation count are env-tunable.
_SELF_RETIRE_DEFAULT_POLL_S = 30.0
_SELF_RETIRE_DEFAULT_CONFIRMATIONS = 3
_SUPERSESSION_DRAIN_TIMEOUT_S = 30.0
_SUPERSESSION_DRAIN_POLL_S = 0.25


def _self_retire_settings() -> tuple[bool, float, int]:
    """``(enabled, poll_seconds, confirmations)`` for generation self-retire.

    ``enabled`` is True (default-ON / opt-out) unless ``AGENT_BRIDGE_SELF_RETIRE``
    is explicitly falsy (``0``/``false``/``no``/``off``) -- when disabled, the loop
    is never created, so no self-retire code runs at all. The poll cadence
    (``AGENT_BRIDGE_SELF_RETIRE_POLL_S``) and confirmation count
    (``AGENT_BRIDGE_SELF_RETIRE_CONFIRMATIONS``) are overridable.
    """
    import os

    enabled = os.environ.get("AGENT_BRIDGE_SELF_RETIRE", "").strip().lower() not in (
        "0", "false", "no", "off",
    )
    try:
        poll = float(
            os.environ.get("AGENT_BRIDGE_SELF_RETIRE_POLL_S", "")
            or _SELF_RETIRE_DEFAULT_POLL_S
        )
        poll = max(1.0, poll)
    except ValueError:
        poll = _SELF_RETIRE_DEFAULT_POLL_S
    try:
        k = int(
            os.environ.get("AGENT_BRIDGE_SELF_RETIRE_CONFIRMATIONS", "")
            or _SELF_RETIRE_DEFAULT_CONFIRMATIONS
        )
        k = max(1, k)
    except ValueError:
        k = _SELF_RETIRE_DEFAULT_CONFIRMATIONS
    return enabled, poll, k


def _same_daemon(left, right) -> bool:
    """Whether two routing endpoints identify the same daemon process."""
    if left is None or right is None:
        return False
    if left.pid is not None and right.pid is not None:
        return left.pid == right.pid and left.port == right.port
    return left.bind == right.bind and left.port == right.port


def _owns_generation(endpoint, owner) -> bool:
    """Whether *endpoint* is the exact active generation published by *owner*."""
    return (
        _same_daemon(endpoint, owner)
        and endpoint.generation == owner.generation
    )


async def _retire_previous_daemon(
    app: FastAPI,
    previous,
    successor,
    *,
    make_client=None,
    drain_timeout: float = _SUPERSESSION_DRAIN_TIMEOUT_S,
) -> None:
    """Drain and gracefully retire the predecessor atomically demoted at start."""
    from .client import BridgeClient, BridgeClientError, BridgeConnectionError
    from .config import config_dir
    from zdd import routing
    from zdd.routing import Endpoint

    def read_active():
        table = routing.read_table(config_dir())
        raw = table.get("active") if isinstance(table, dict) else None
        return Endpoint.from_dict(raw) if isinstance(raw, dict) else None

    active = await asyncio.to_thread(read_active)
    if not _owns_generation(active, successor):
        log.info(
            "Skipping predecessor retirement because generation %d is no "
            "longer active", successor.generation,
        )
        return

    if make_client is None:
        make_client = lambda endpoint: BridgeClient(
            endpoint.base_url,
            app.state.auth_token,
            timeout=max(1, int(drain_timeout + 30)),
            connect_grace=0,
        )
    client = make_client(previous)

    async def release_drain(message: str) -> None:
        try:
            await _run_in_daemon_thread(client.undrain)
        except (BridgeClientError, BridgeConnectionError, OSError):
            log.warning(message, exc_info=True)

    try:
        result = await _run_in_daemon_thread(
            functools.partial(
                client.drain,
                timeout=drain_timeout,
                poll=_SUPERSESSION_DRAIN_POLL_S,
                force=False,
                source="startup-supersession",
                reason="superseded by a normal daemon start",
            )
        )
    except (BridgeClientError, BridgeConnectionError, OSError):
        log.warning(
            "Could not drain superseded predecessor at %s; generation "
            "self-retire remains the backstop",
            previous.base_url,
            exc_info=True,
        )
        await release_drain(
            "Failed to release predecessor drain after drain request failure"
        )
        return

    if not result.get("drained", False):
        log.warning(
            "Superseded predecessor at %s did not reach a safe drain boundary "
            "within %.0fs; leaving it alive",
            previous.base_url,
            drain_timeout,
        )
        await release_drain(
            "Failed to release predecessor drain after drain timeout"
        )
        return

    active = await asyncio.to_thread(read_active)
    if _same_daemon(active, previous):
        log.warning(
            "Superseded predecessor became active again while draining; "
            "releasing its drain gate instead of shutting it down"
        )
        await release_drain("Failed to release restored predecessor drain")
        return
    if active is None or active.generation < successor.generation:
        log.warning(
            "Routing ownership became ambiguous while draining the predecessor; "
            "releasing its drain gate instead of shutting it down"
        )
        await release_drain("Failed to release predecessor drain")
        return

    try:
        await _run_in_daemon_thread(client.shutdown)
    except (BridgeClientError, BridgeConnectionError, OSError):
        log.warning(
            "Could not request graceful shutdown of superseded predecessor at %s",
            previous.base_url,
            exc_info=True,
        )
        await release_drain(
            "Failed to release predecessor drain after shutdown request failure"
        )


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
        from .relay_state import set_live_relay_port
        set_live_relay_port(relay_server.port)
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

    lifecycle_start_task = None
    # Record the *actually-running* daemon version so the launch-path reconciler
    # can detect a daemon lagging its installed plugin even when the on-disk
    # deploy manifest already matches (dotfiles #533). Only the **primary** daemon
    # writes it: the elevated sub-daemon (enable_credential_relay=False) shares the
    # runtime dir and would otherwise clobber the marker with a non-primary pid.
    if getattr(cfg, "enable_credential_relay", True):
        from .runtime_version import write_running_version

        write_running_version()

    db_path = Path(cfg.db_path).expanduser()
    db = Database(db_path)
    db.start_writer()
    app.state.db = db

    mgr = session_manager_from_config(db, cfg)
    app.state.session_manager = mgr
    app.state.resolver = AgentResolver({}, {})
    mgr.set_resolver(app.state.resolver)
    app.state.ready = False
    app.state.readiness_error = None
    app.state.readiness_exception = None
    app.state.topology_ready = False
    app.state.credential_relay_ready = False

    # The launch path reserved the serving socket before entering lifespan.
    # Publish that concrete endpoint before topology, relay, or remote recovery
    # work so clients immediately follow this generation and the prior active
    # daemon becomes legacy. A passive ZDD cutover instance stays silent: its
    # orchestrator owns the health-gated routing flip.
    published_endpoint = None
    previous_endpoint = None
    if getattr(app.state, "publish_on_ready", False):
        import os as _os

        from . import __version__ as _ver
        from . import lifecycle_hooks
        from .config import config_dir
        from zdd import routing

        _bound_port = getattr(app.state, "bound_port", cfg.port)
        await asyncio.to_thread(lifecycle_hooks.startup_sweep, config_dir())
        try:
            published_endpoint, previous_endpoint = await asyncio.to_thread(
                routing.publish_active_with_previous,
                config_dir(),
                bind=cfg.bind,
                port=_bound_port,
                pid=_os.getpid(),
                version=_ver,
                demote_existing=True,
            )
        except Exception as exc:
            await asyncio.to_thread(db.close)
            log.exception("Failed to publish the bound daemon endpoint")
            raise RuntimeError(
                "Cannot start agent-bridge without publishing its active endpoint"
            ) from exc

    # Reattach to any Session Hosts that survived a prior frontend restart
    # (goal 3), instead of leaving those sessions STOPPED. Best-effort and, per
    # its own contract, it MUST NOT block daemon startup -- yet awaiting it here
    # did: a *slow* far-side authority recovery (an unreachable remote venue can
    # stall reattach for the whole remote-recovery budget, ~30s) delayed serving
    # and could push startup past the self-watchdog grace (#166), triggering a
    # reap/restart flap and a stale active.json (dotfiles #1932 / the upstream
    # flapping report). Run it as a background task so the daemon serves
    # immediately; surviving sessions reattach concurrently, moments after
    # startup. The task is cancelled on shutdown.
    async def _reattach_session_hosts_bg() -> None:
        try:
            n = await mgr.reattach_session_hosts()
            if n:
                logging.getLogger("agent-bridge").info(
                    "Reattached %d session(s) to surviving Session Hosts", n
                )
        except asyncio.CancelledError:
            # Normal on shutdown (the task is cancelled) -- never a failure.
            raise
        except Exception:
            logging.getLogger("agent-bridge").warning(
                "Session-Host reattach on startup failed", exc_info=True
            )

    reattach_task = asyncio.create_task(_reattach_session_hosts_bg())

    relay_server = None
    relay_start_lock = asyncio.Lock()

    async def _ensure_relay():
        nonlocal relay_server
        async with relay_start_lock:
            relay_server = await _start_credential_relay(app)
            app.state.credential_relay_ready = bool(
                relay_server is not None
                and getattr(relay_server, "running", False)
            )
            return relay_server

    async def _initialize_readiness() -> None:
        retry_delay = 0.25
        while True:
            try:
                # Topology/provider discovery can shell out and touch
                # remote-facing state. Swap the fully-built resolver in once.
                resolver = (
                    await _run_in_daemon_thread(daemon_resolver, cfg)
                    if getattr(app.state, "background_readiness", False)
                    else daemon_resolver(cfg)
                )
                app.state.resolver = resolver
                mgr.set_resolver(resolver)
                app.state.topology_ready = True

                if resolver.agents or resolver.machines:
                    from .routes.worktrees import get_cache
                    wt_cache = get_cache()
                    wt_cache.configure(interval=cfg.worktree_discovery_interval)
                    wt_cache.start(resolver)

                if not getattr(cfg, "enable_credential_relay", True):
                    log.info(
                        "Credential relay disabled for this daemon "
                        "(enable_credential_relay=False) -- reusing the primary daemon's relay"
                    )
                else:
                    await _ensure_relay()

                session_count = len(mgr.list_sessions())
                app.state.readiness_error = None
                app.state.readiness_exception = None
                log.info(
                    "agent-bridge ready (port=%s, db=%s, sessions=%d)",
                    cfg.port, db_path, session_count,
                )
                app.state.ready = True
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                app.state.ready = False
                app.state.readiness_error = "initialization failed"
                app.state.readiness_exception = exc
                log.error("Background readiness initialization failed", exc_info=True)
                if not getattr(app.state, "background_readiness", False):
                    return
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 5.0)

    readiness_task = asyncio.create_task(_initialize_readiness())
    if not getattr(app.state, "background_readiness", False):
        await readiness_task
        if app.state.readiness_error:
            readiness_exception = app.state.readiness_exception
            reattach_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reattach_task
            if published_endpoint is not None:
                await asyncio.to_thread(
                    routing.restore_previous_if_owner,
                    config_dir(),
                    pid=published_endpoint.pid,
                    generation=published_endpoint.generation,
                )
            await asyncio.to_thread(db.close)
            raise RuntimeError(str(readiness_exception)) from readiness_exception

    previous_retire_task = None
    if published_endpoint is not None and previous_endpoint is not None:
        previous_retire_task = asyncio.create_task(
            _retire_previous_daemon(
                app,
                previous_endpoint,
                published_endpoint,
                make_client=getattr(
                    app.state, "supersession_client_factory", None
                ),
                drain_timeout=getattr(
                    app.state,
                    "supersession_drain_timeout",
                    _SUPERSESSION_DRAIN_TIMEOUT_S,
                ),
            )
        )

    # Expose a relay-adoption hook so a passive cutover instance can bind the
    # shared relay port *after* the retiring daemon releases it (the relay is a
    # singleton on 9857). The /api/v1/relay/adopt endpoint calls this.
    async def _adopt_relay():
        await _ensure_relay()
        return relay_server is not None and getattr(relay_server, "running", False)

    app.state.adopt_relay = _adopt_relay

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
            # lost turn.
            try:
                await mgr.recover_disconnected_hosts()
            except Exception:
                log.warning("Liveness-driven reattach failed", exc_info=True)
            # Reconcile each host-backed session's reapable state to its
            # host so a subsequently-lost front can self-reap an idle child
            # (#51). Backstop beneath the precise turn-boundary pushes.
            try:
                await mgr.refresh_host_reapable()
            except Exception:
                log.warning("Host reapable-state refresh failed", exc_info=True)
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
    # on-disk install for a whole frontend lifetime. Harmless (empty) unless a
    # breaking host-protocol change left older hosts running.
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
    # TTL, freeing their Copilot children (resumable via replay). Only runs with
    # a positive TTL configured.
    idle_reap_task = None
    if cfg.idle_reap_ttl_seconds > 0:
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
    # whose heartbeat lease has lapsed. A lapsed lease is reconciled against the
    # CLI's real process liveness: a gone process is expired (a dead CLI cannot
    # leave a worktree un-ownable or accept a racing steer) and its inbox
    # messages dropped; a still-alive process (extension stopped heartbeating --
    # a wedged session) is marked ``wedged`` so it stays legible/reclaimable
    # instead of vanishing (#3145). Long-dead rows are then purged so the
    # registry does not accumulate a graveyard (#3144). Cheap; always on. Sweeps
    # at half the lease window so a lapsed row is reconciled within ~one window.
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
                        "Live-session reaper demoted %d lapsed registration(s) "
                        "(expired/wedged)", n
                    )
            except Exception:
                log.warning("Live-session lease reap failed", exc_info=True)

    live_reap_task = asyncio.create_task(_live_reap_loop())

    # The route was published before slow startup work. Record the durable START
    # event only after uvicorn confirms that it is accepting connections.
    if getattr(app.state, "publish_on_ready", False):
        async def _record_start_when_listening() -> None:
            server = getattr(app.state, "uvicorn_server", None)
            # Lifespan completes before uvicorn marks the server started, so
            # this confirmation must remain a background task.
            for _ in range(600):  # ~60s ceiling
                if server is not None and getattr(server, "started", False):
                    break
                await asyncio.sleep(0.1)
            started = server is not None and getattr(server, "started", False)
            if not started:
                log.warning(
                    "Daemon endpoint was published but uvicorn did not report "
                    "started within 60s"
                )
                return
            app.state.lifecycle_started = await asyncio.to_thread(
                lifecycle_hooks.record_start, config_dir(), _ver, _bound_port
            )

        lifecycle_start_task = asyncio.create_task(_record_start_when_listening())

    # Self-retire on supersession (owner-liveness tether). A daemon that has been
    # *demoted* -- a newer generation flipped the routing table and now serves
    # clients -- drains and exits on its own instead of lingering as a stranded
    # ``serve --passive`` process (the observed leak: a demoted generation
    # persisting for hours after its successor took over). The "owner" being
    # tracked here is the single active routing generation, with the periodic
    # reapers as a backstop.
    #
    # DEFAULT-ON (opt-out): armed unless ``AGENT_BRIDGE_SELF_RETIRE`` is explicitly
    # falsy; when disabled, none of the loop below is created or runs. The loop **self-gates on
    # active-ness**: its startup phase waits until the routing table's ``active``
    # entry is *our own pid* before it captures our generation and begins watching.
    # This is what makes it correct for a **cutover-promoted** daemon -- one spawned
    # ``--passive`` (so ``publish_on_ready`` is False) and promoted by the
    # orchestrator flipping the routing table to it: such a daemon is exactly the
    # ``serve --passive`` process this targets, so we must NOT gate on
    # ``publish_on_ready`` (that would leave the primary target inert). A passive
    # instance that is never promoted simply never sees its own pid as active and
    # arms nothing. Fail-safe on two independent axes -- it exits only once BOTH
    # (a) supersession by a *live, strictly-newer* generation and (b) local idleness
    # are K-confirmed. So the genuinely-active daemon (which reads its own pid as
    # active) can never self-retire, and an in-flight turn on a demoted daemon is
    # never cut mid-flight: it drains as clients follow the flipped route.
    self_retire_task = None
    _sr_enabled, _sr_poll, _sr_confirmations = _self_retire_settings()
    if _sr_enabled:
        async def _self_retire_loop() -> None:
            import os as _os

            from zdd import routing
            from zdd.routing import Endpoint

            from .config import config_dir
            from .self_retire import is_superseded

            my_pid = _os.getpid()
            # Observe our own publish landing first, capturing our generation.
            # Until we are the recorded active we cannot meaningfully be
            # "superseded"; a passive instance that is never promoted simply
            # never arms the watch (returns without ever calling is_superseded).
            my_gen: int | None = None
            for _ in range(600):  # ~5 min ceiling to see our own publish
                await asyncio.sleep(0.5)
                data = await asyncio.to_thread(routing.read_table, config_dir())
                raw = data.get("active") if isinstance(data, dict) else None
                ep = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
                if ep is not None and ep.pid == my_pid:
                    my_gen = ep.generation
                    break
            if my_gen is None:
                return
            confirms = 0
            while True:
                await asyncio.sleep(_sr_poll)
                try:
                    superseded = await asyncio.to_thread(
                        is_superseded, config_dir(), my_pid, my_gen
                    )
                    idle = superseded and (
                        await asyncio.to_thread(_count_active_sessions, mgr, db) == 0
                    )
                except Exception:
                    confirms = 0
                    log.debug("Self-retire supersession check failed", exc_info=True)
                    continue
                if not (superseded and idle):
                    confirms = 0  # any miss resets: only a sustained state acts
                    continue
                confirms += 1
                if confirms >= _sr_confirmations:
                    log.info(
                        "Superseded by a live newer generation and idle -- "
                        "self-retiring (was gen %d, pid %d)", my_gen, my_pid,
                    )
                    server = getattr(app.state, "uvicorn_server", None)
                    if server is not None:
                        server.should_exit = True
                    return

        self_retire_task = asyncio.create_task(_self_retire_loop())
        log.info(
            "Self-retire-on-supersession armed (K=%d, poll=%.0fs)",
            _sr_confirmations, _sr_poll,
        )

    yield

    # Shutdown: retract our routing-table claim so clients fall back (or follow
    # a successor that already flipped the table). Done first so no new client
    # is routed to us while we tear sessions down.
    if lifecycle_start_task is not None:
        lifecycle_start_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lifecycle_start_task
    if previous_retire_task is not None:
        # Do not abandon an in-flight drain request: its daemon thread cannot
        # be cancelled, and leaving after it opens the predecessor's drain gate
        # would strand that daemon closed. The request is already bounded, so
        # finish the handshake and let it either shut down or undrain the old
        # daemon before this generation retracts its route.
        try:
            await asyncio.shield(previous_retire_task)
        except asyncio.CancelledError:
            await previous_retire_task
    # Shutdown: stop the generation self-retire watch (if armed)
    if self_retire_task is not None:
        self_retire_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self_retire_task
    readiness_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await readiness_task
    import os as _os

    from zdd import routing
    from . import lifecycle_hooks
    from .config import config_dir
    # STOP only if we emitted a matching START (the server confirmed
    # listening), so a daemon that never actually served records neither.
    if getattr(app.state, "lifecycle_started", False):
        await asyncio.to_thread(lifecycle_hooks.record_stop, config_dir())
    # Every daemon clears only a route it currently owns. This is a no-op for an
    # unpromoted passive instance, but lets a passive instance promoted by the
    # cutover orchestrator retract its route on a later graceful shutdown.
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

    # Shutdown: stop the background Session-Host reattach if still in flight
    reattach_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reattach_task

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

    # Shutdown: prepare in-flight turns for the frontend restart. By default
    # (dotfiles#1661) this is DETACH-ONLY -- it does NOT cancel the remote
    # agent's turn; the Session Host keeps running it and the successor frontend
    # reattaches and continues the SAME turn. Only the opt-in
    # `cancel_turns_on_redeploy` restores the legacy cancel-then-Resume.
    try:
        await mgr.graceful_cancel_for_redeploy()
    except Exception:
        log.warning("Graceful-cancel on shutdown failed", exc_info=True)

    # Shutdown: detach all active sessions (host + child + turn survive for
    # reattach). `cancel_turn` mirrors the redeploy policy: detach-only by
    # default, cancel only if `cancel_turns_on_redeploy` is set.
    for session in mgr.list_sessions():
        if session.client and session.client.is_running:
            try:
                log.info("Detaching session %s on shutdown", session.session_id)
                await mgr.stop_session(
                    session.session_id,
                    cancel_turn=mgr.cancel_turns_on_redeploy,
                )
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

    # Install a telemetry sink if one is configured (generic open hook; a no-op
    # unless a consumer wired a sink). Prefer a convention-located config file
    # (env-free); fall back to the environment so env-wired deploys don't
    # regress. Fail-open either way.
    if not telemetry.load_sink_from_config():
        telemetry.load_sink_from_env()

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
    app.include_router(remote.router)
    app.include_router(agents.router)
    app.include_router(worktrees.router)
    app.include_router(admin.router)

    return app
