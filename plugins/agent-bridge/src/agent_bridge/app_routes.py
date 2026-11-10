"""Ordinary application route and middleware registration."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from repository_policy import AgentHostingError

from . import __version__, worktree_native_resume
from .auth import BearerAuthMiddleware
from .auth_custody import AuthCustody, AuthCustodyMiddleware
from .live_representation import LiveEventStore
from .live_inbox_runtime import LiveInboxRuntime
from .live_questions import LiveQuestions
from .live_delivery_state import LiveDeliveryState
from .mux_questions import MuxQuestions
from .models import ServiceConfig
from .routes import (
    acquisitions, acp_ws, admin, agents, dispatch_tasks, health, live_delivery_state,
    live_inbox, live_questions, live_sessions,
    mux_questions, owner_membership, remote, session_observations, sessions, status, ui, worktrees,
)


def configure_serving_app(
    cfg: ServiceConfig, auth_token: str, *, lifespan, auth_custody: AuthCustody | None = None,
) -> FastAPI:
    """Register the ordinary serving surfaces with the caller's lifespan."""
    app = FastAPI(
        title="Agent Bridge",
        description="Persistent inter-agent communication service",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.config = cfg

    @app.exception_handler(AgentHostingError)
    async def hosting_refused(_request: Request, exc: AgentHostingError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": {"code": exc.code, "message": str(exc)}},
        )

    # Websocket transport bypasses the HTTP auth middleware.
    app.state.auth_token = auth_token
    app.state.auth_custody = auth_custody if auth_custody is not None else AuthCustody()
    app.state.live_event_store = LiveEventStore()
    app.state.live_inbox_runtime = LiveInboxRuntime()
    app.state.live_questions = LiveQuestions()
    app.state.live_delivery_state = LiveDeliveryState()
    app.state.mux_questions = MuxQuestions()
    from .status_runtime import StatusRuntime
    app.state.status_runtime = StatusRuntime(app)
    app.add_middleware(BearerAuthMiddleware, token=auth_token)
    app.add_middleware(AuthCustodyMiddleware, custody=app.state.auth_custody)
    for router in (
        worktree_native_resume.router,
        acquisitions.router, health.router, ui.router, acp_ws.router,
        sessions.router, session_observations.router, owner_membership.router, live_sessions.router,
        live_inbox.router, live_questions.router, live_delivery_state.router,
        mux_questions.router, remote.router, agents.router,
        worktrees.router, admin.router, dispatch_tasks.router,
    ):
        app.include_router(router)
    # Status has no app-specific dependencies. Reuse its compiled routes rather
    # than rebuilding their validation graph inside candidate preparation.
    app.router.routes.extend(status.router.routes)
    return app
