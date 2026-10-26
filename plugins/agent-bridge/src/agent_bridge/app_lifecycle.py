"""Process admission and settled database shutdown for the Bridge ASGI host."""

import asyncio
from contextlib import asynccontextmanager
import logging
import threading

from agent_procutil.daemon_instances import admit_daemon_instance

log = logging.getLogger(__name__)


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
