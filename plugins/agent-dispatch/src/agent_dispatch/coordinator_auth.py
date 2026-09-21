"""Authentication helpers for coordinator-only routes."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


def _make_auth(token: str | None, control_token: str | None):
    bearer = HTTPBearer(auto_error=False)
    accepted = tuple(value for value in (token, control_token) if value)

    def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:  # noqa: B008
        if token is None:
            return
        if creds is None or not any(
            secrets.compare_digest(creds.credentials, value) for value in accepted
        ):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return check


def _make_control_auth(
    control_token: str | None,
    on_reject: Callable[[str, dict[str, object]], None],
):
    bearer = HTTPBearer(auto_error=False)

    def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:  # noqa: B008
        if control_token is None:
            detail = {
                "code": "producer_control_unavailable",
                "operation": "transition",
                "reason": "control_authority_not_configured",
                "message": "managed producer transitions require a configured control token",
                "retryable": False,
            }
            on_reject(
                "producer_scope.transition_rejected",
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
                "code": "producer_control_forbidden",
                "operation": "transition",
                "reason": "invalid_control_authority",
                "message": "invalid or missing producer control bearer",
                "retryable": False,
            }
            on_reject(
                "producer_scope.transition_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(
                status_code=403,
                detail=detail,
            )

    return check
