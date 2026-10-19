"""Opt-in candidate application; no serving ownership or adoption authority."""

from __future__ import annotations

from contextlib import asynccontextmanager
import sqlite3

from fastapi import FastAPI
from starlette.types import Receive, Scope, Send
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal

from . import __version__
from .db import Database
from .launch_runtime import executing_executable
from .runtime_restore import RestoredRuntime


class _CandidateApp(FastAPI):
    _serving_target: FastAPI | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target = self._serving_target
        if scope["type"] != "lifespan" and target is not None:
            await target(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


def create_restored_app(runtime: RestoredRuntime) -> FastAPI:
    """Prepare a health-only passive candidate from validated private state.

    This factory does not bind a listener, publish routing, start the relay or
    grant native custody. Those remain separate, receiver-bound adoption gates.
    """
    if type(runtime) is not RestoredRuntime:
        raise PrivateIPCError(Refusal.AUTHORITY)

    @asynccontextmanager
    async def candidate_lifespan(app: FastAPI):
        db = None
        app.state.candidate_ready = False
        try:
            try:
                runtime.validate_configuration()
                if runtime.relay is not None and runtime.relay.running:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                app.state.runtime_executable = executing_executable()
                db = Database(runtime.config.db_path, read_only=True)
                manager = runtime.build_manager(db)
            except PrivateIPCError:
                raise
            except (OSError, ValueError, sqlite3.Error):
                raise PrivateIPCError(Refusal.AUTHORITY) from None
            app.state.db = db
            app.state.session_manager = manager
            app.state.candidate_ready = True
            yield
        finally:
            app.state.candidate_ready = False
            app.state.ready = False
            app.state.credential_relay_ready = False
            app._serving_target = None
            adopted = getattr(app.state, "db", None)
            pending = getattr(app.state, "pending_runtime", None)
            if pending is not None:
                adopted = pending._writable_database
            try:
                if pending is not None:
                    try:
                        await pending.close_idle_sessions()
                    finally:
                        await pending.close_relay(Deadline(5))
            finally:
                try:
                    if adopted is not None and adopted is not db:
                        adopted.close()
                finally:
                    try:
                        if db is not None:
                            db.close()
                    finally:
                        if pending is not None:
                            pending.close()

    app = _CandidateApp(
        title="Agent Bridge Candidate", version=__version__,
        lifespan=candidate_lifespan, openapi_url=None, docs_url=None, redoc_url=None,
    )
    app.state.config = runtime.config
    app.state.auth_token = runtime.auth_token
    app.state.runtime_origins = runtime.origins
    app.state.restored_runtime = runtime
    if runtime.authentication is not None:
        runtime.authentication.bind(app)
    if runtime.live_events is not None:
        app.state.live_event_store = runtime.live_events
    app.state.resolver = runtime.resolver
    app.state.credential_relay = runtime.relay
    app.state.passive = True
    app.state.publish_on_ready = False
    app.state.ready = False
    app.state.candidate_ready = False
    app.state.topology_ready = False
    app.state.credential_relay_ready = False
    if runtime.worktrees is not None:
        app.state.worktree_discovery_cache = runtime.worktrees

    @app.get("/health")
    async def candidate_health() -> dict[str, object]:
        return {
            "status": "candidate",
            "service": "agent-bridge",
            "ready": False,
            "candidate_ready": app.state.candidate_ready,
            "passive": True,
        }

    return app
