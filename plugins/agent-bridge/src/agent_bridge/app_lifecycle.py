"""Process admission and settled database shutdown for the Bridge ASGI host."""

import asyncio
from contextlib import asynccontextmanager
import contextvars
import functools
import logging
import sys
import threading

from agent_procutil.daemon_instances import admit_daemon_instance

log = logging.getLogger(__name__)


async def settled_thread_call(function, *args, **kwargs):
    """Keep native side effects owned until their worker finishes on cancellation."""
    owner = asyncio.get_running_loop().run_in_executor(
        None, functools.partial(contextvars.copy_context().run, function, *args, **kwargs),
    )
    cancellation = None
    while True:
        try:
            result = await asyncio.shield(owner)
            break
        except asyncio.CancelledError as error:
            cancellation = error
    if cancellation is not None:
        raise cancellation
    return result


async def start_credential_relay(app, *, start_listener: bool = True):
    """Register credential sources and idempotently start the shared listener."""
    from .app import _run_in_daemon_thread

    existing = getattr(app.state, "credential_relay", None)
    if start_listener and existing is not None and getattr(existing, "running", False):
        return existing
    relay_server = None
    try:
        from credential_relay import RelayBuilder

        from .agent_registry import register_credential_sources
        from .http_provider_sources import register_http_credential_sources

        builder = RelayBuilder()
        register_sources = register_credential_sources if start_listener else register_http_credential_sources
        await _run_in_daemon_thread(register_sources, builder)
        manager = getattr(app.state, "session_manager", None)
        if manager is not None and manager._remote_credential_policy is not None:
            manager._remote_credential_policy.register_sources(builder.http_sources)
        if not start_listener:
            return None
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
        # A bind conflict leaves tunneled authentication unavailable.
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


def writer_running(db) -> bool:
    """Observe the existing database writer under its native ownership lock."""
    with db._writer_state_lock:
        thread = db._writer_thread
        return thread is not None and thread.is_alive()


def _close_database(db, completed):
    pause = threading.Event()
    try:
        while True:
            try:
                db.close()
            except Exception:
                if not writer_running(db):
                    log.warning("Failed to stop event writer cleanly", exc_info=True)
                    return
                log.warning("Event writer still retiring; retaining admission", exc_info=True)
            if not writer_running(db):
                return
            while writer_running(db):
                pause.wait(.05)
            # A bounded close refused while its writer was alive. Retry only
            # after that actual thread retires, to finish the database close.
    finally:
        completed.set()


async def close_database(db):
    """Keep one native close owner alive through cancellation and writer timeout."""
    completed = threading.Event()
    owner = asyncio.get_running_loop().run_in_executor(None, _close_database, db, completed)
    cancellation = None
    while True:
        try:
            await asyncio.shield(owner)
            break
        except asyncio.CancelledError as error:
            cancellation = error
            if owner.cancelled():
                # A cancelled asyncio facade is not native completion evidence.
                while not completed.is_set():
                    try:
                        await asyncio.sleep(.05)
                    except asyncio.CancelledError as error:
                        cancellation = error
                break
    if cancellation is not None:
        raise cancellation


@asynccontextmanager
async def lifespan(app):
    """Retain admission through application startup, service and shutdown."""
    with admit_daemon_instance("agent-bridge", source=__file__, purpose="production"):
        from .app import _lifespan

        try:
            async with _lifespan(app):
                yield
        finally:
            try:
                from .app_agent_roster import stop_agent_roster_cache

                await stop_agent_roster_cache(app)
            finally:
                db = getattr(app.state, "db", None)
                if db is not None:
                    await close_database(db)
