"""Typed HTTP refusals for session-start failures raised before launch."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from ..local_launch_preparation import LocalLaunchPreparationError

__all__ = ["LocalLaunchPreparationError", "start_refusal"]


def start_refusal(
    exc: Exception, *, agent_name: str, target_dir: object, log: logging.Logger
) -> HTTPException:
    """Map a pre-launch session-start failure to its typed HTTP refusal."""
    if isinstance(exc, LocalLaunchPreparationError):
        # Resolution failed before registration or launch. Surface the cause
        # as a typed refusal (and log it) instead of an opaque 500 whose
        # traceback only reaches the daemon's stderr.
        log.error(
            "Local launch preparation failed for agent %r (target dir %r): %s",
            agent_name, target_dir, exc, exc_info=exc,
        )
        return HTTPException(
            status_code=422,
            detail={
                "code": "local_launch_preparation_failed",
                "message": str(exc),
                "effect": "not_started",
            },
        )
    return HTTPException(status_code=503, detail=str(exc))
