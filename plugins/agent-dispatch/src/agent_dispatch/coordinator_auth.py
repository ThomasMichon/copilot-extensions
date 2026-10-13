"""Authentication helpers for coordinator-only routes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


def scoped_control_token(control_token: str, scope: str) -> str:
    """Derive a bearer scoped to one producer-owned operation."""
    return hmac.new(
        control_token.encode("utf-8"),
        scope.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


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
