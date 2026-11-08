"""Authentication helpers for coordinator-only routes."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse

from .control_token import scoped_control_token  # noqa: F401 -- compatible public export
from .task_scope import TASK_SCOPE_HEADER, TaskScopeTokenError, verify_task_scope_token

TASK_SCOPE_ROUTES = frozenset({
    ("GET", "/tasks/{task_id}"),
    ("GET", "/tasks/{task_id}/payload"),
    ("GET", "/tasks/{task_id}/pool-start/contract"),
    ("POST", "/tasks/{task_id}/start"),
    ("POST", "/tasks/{task_id}/pool-start"),
    ("POST", "/tasks/{task_id}/progress"),
    ("POST", "/tasks/{task_id}/heartbeat"),
    ("POST", "/tasks/{task_id}/complete"),
    ("POST", "/tasks/{task_id}/abandon"),
})


def install_scope_boundary(app, is_active):
    @app.middleware("http")
    async def deny_scoped_non_task_surfaces(request: Request, call_next):
        # Mounted apps and generated documentation bypass APIRoute dependencies.
        path = request.url.path
        if is_active is not None and path not in {"/health", "/drain", "/undrain", "/shutdown"}:
            from .coordinator_loops import _route_active

            if not await _route_active(is_active):
                return JSONResponse(
                    status_code=503, content={"detail": "coordinator does not own the active route"},
                )
        if TASK_SCOPE_HEADER in request.headers and (
            path == "/mcp" or path.startswith("/mcp/")
            or path in {app.docs_url, app.redoc_url, app.openapi_url, "/docs/oauth2-redirect"}
        ):
            return JSONResponse(
                status_code=403, content={"detail": "route does not accept task authority"},
            )
        return await call_next(request)


def _make_auth(token: str | None, control_token: str | None):
    bearer = HTTPBearer(auto_error=False)
    accepted = tuple(value for value in (token, control_token) if value)

    def check(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    ) -> None:
        raw = request.headers.get(TASK_SCOPE_HEADER)
        if raw is not None:
            if not control_token:
                raise HTTPException(status_code=401, detail="task-scoped authority unavailable")
            try:
                scope = verify_task_scope_token(control_token, raw)
            except TaskScopeTokenError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            route = request.scope.get("route")
            if (request.method, getattr(route, "path", None)) not in TASK_SCOPE_ROUTES:
                raise HTTPException(status_code=403, detail="route does not accept task authority")
            request.state.task_scope = scope
            from .task_authority import resolve_task_authority

            resolve_task_authority(
                request, request.app.state.queue, request.app.state.task_fencing_guard,
                request.path_params["task_id"], None,
            )
            return
        if token is None:
            return
        if creds is not None and any(
            secrets.compare_digest(creds.credentials, value) for value in accepted
        ):
            return
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return check


def _make_control_auth(
    control_token: str | None,
    on_reject: Callable[[str, dict[str, object]], None],
    *,
    operation: str = "transition",
    rejection_event: str = "producer_scope.transition_rejected",
    code_prefix: str = "producer_control",
    unavailable_message: str = "managed producer transitions require a configured control token",
    forbidden_message: str = "invalid or missing producer control bearer",
):
    bearer = HTTPBearer(auto_error=False)

    def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:  # noqa: B008
        if control_token is None:
            detail = {
                "code": f"{code_prefix}_unavailable",
                "operation": operation,
                "reason": "control_authority_not_configured",
                "message": unavailable_message,
                "retryable": False,
            }
            on_reject(
                rejection_event,
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(
                status_code=503,
                detail=detail,
            )
        if creds is None or not secrets.compare_digest(
            creds.credentials, control_token
        ):
            detail = {
                "code": f"{code_prefix}_forbidden",
                "operation": operation,
                "reason": "invalid_control_authority",
                "message": forbidden_message,
                "retryable": False,
            }
            on_reject(
                rejection_event,
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(
                status_code=403,
                detail=detail,
            )

    return check
