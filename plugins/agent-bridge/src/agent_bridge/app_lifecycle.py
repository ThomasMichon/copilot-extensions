"""Process admission for the Bridge ASGI application lifecycle."""

from contextlib import asynccontextmanager

from agent_procutil.daemon_instances import admit_daemon_instance


@asynccontextmanager
async def lifespan(app):
    """Retain admission through application startup, service and shutdown."""
    with admit_daemon_instance("agent-bridge", source=__file__, purpose="production"):
        from .app import _lifespan

        async with _lifespan(app):
            yield
