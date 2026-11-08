"""Staged authenticated foreground serving, separate from post-exit adoption."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from zdd.private_protocol import PrivateIPCError, Refusal

from .auth import BearerAuthMiddleware
from .db import Database
from .runtime_restore import RestoredRuntime


class CandidateServing:
    """Own one prepared ASGI target and its initially idle event writer.

    Preparation runs outside routing authority and exposes no routes. Activation
    is a short receiver-fenced commit. No topology rediscovery, cache refresh,
    relay binding, host attachment, maintenance or retirement is performed.
    """

    def __init__(self, app: FastAPI, runtime: RestoredRuntime) -> None:
        from .app_routes import configure_serving_app

        if (
            runtime.roster is None or not runtime.roster.ready()
            or (runtime.sessions is not None and runtime.sessions.sessions)
            or app.state.session_manager._sessions
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

        @asynccontextmanager
        async def unused_lifespan(target: FastAPI):
            raise PrivateIPCError(Refusal.AUTHORITY)
            yield  # The candidate, not this detached target, owns lifespan.

        self._app = app
        self._db = app.state.db
        self._manager = app.state.session_manager
        self._runtime = runtime
        self._target = configure_serving_app(
            runtime.config, runtime.auth_token, lifespan=unused_lifespan,
            auth_custody=runtime.authentication.custody if runtime.authentication is not None else None,
        )
        self._store = (
            runtime.live_events if runtime.live_events is not None
            else self._target.state.live_event_store
        )
        self._target.state = app.state
        self._target.middleware_stack = self._target.build_middleware_stack()
        self._stack = self._target.middleware_stack
        self._middleware = self._middleware_signature()
        self._routes = self._route_signature()
        self._writer: threading.Thread | None = None
        self.active = False
        self.prepared = False

    def _route_signature(self) -> tuple:
        return tuple(
            (route, getattr(route, "path", None), getattr(route, "endpoint", None),
             frozenset(getattr(route, "methods", ()) or ()), getattr(route, "app", None))
            for route in self._target.routes
        )

    def _middleware_signature(self) -> tuple:
        current = self._target.middleware_stack
        layers = []
        for _ in range(16):
            child = getattr(current, "app", None)
            layers.append((
                current, child,
                current._token if type(current) is BearerAuthMiddleware else None,
            ))
            if current is self._target.router:
                return tuple(layers)
            if child is None:
                break
            current = child
        raise PrivateIPCError(Refusal.AUTHORITY)

    def prepare_writer(self) -> None:
        """Start only the idle database worker, before any API surface is reachable."""
        if self._writer is not None or type(self._db) is not Database:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._db.start_writer()
        self._writer = self._db._writer_thread
        self.verify()

    def verify(self) -> None:
        """Require the actual retained graph, warmed roster and healthy writer."""
        state = self._app.state
        if self._runtime.authentication is not None:
            from .auth_runtime import verify_auth_consumers

            self._runtime.authentication.verify(self._app)
            verify_auth_consumers(
                self._target, self._runtime.authentication.custody, self._runtime.auth_token,
            )
        if (
            self._writer is None or self._db._writer_thread is not self._writer
            or not self._writer.is_alive() or self._db._writer_error is not None
            or state.db is not self._db or self._db.read_only
            or state.session_manager is not self._manager
            or (not self.active and self._manager._sessions)
            or self._target.state is not state
            or self._target.middleware_stack is not self._stack
            or self._middleware_signature() != self._middleware
            or self._route_signature() != self._routes
            or self._runtime.roster is None or not self._runtime.roster.ready()
            or (
                self.active
                and (state.agent_roster_cache is not self._runtime.roster
                     or state.live_event_store is not self._store
                     or self._app._serving_target is not self._target
                     or state.topology_ready is not True)
            )
            or (
                not self.active
                and (getattr(self._app, "_serving_target", None) is not None
                     or self._db._event_write_q.unfinished_tasks)
            )
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def activate(self) -> None:
        """Commit an already prepared graph; caller must hold receiver authority."""
        self.verify()
        if self.active or not self.prepared:
            raise PrivateIPCError(Refusal.AUTHORITY)
        state = self._app.state
        state.agent_roster_cache = self._runtime.roster
        state.live_event_store = self._store
        state.topology_ready = True
        state.readiness_error = None
        state.readiness_exception = None
        state.passive = False
        self._app._serving_target = self._target
        self.active = True
        state.ready = True
