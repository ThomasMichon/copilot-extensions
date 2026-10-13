"""Refuse unsupported prompt transports before the engine can stage an intent."""

from __future__ import annotations

from . import engine_client


def require_supported_seed(req) -> None:
    if not getattr(req, "seed_prompt", None):
        return
    if (
        req.mode not in {"new", "resume"}
        or getattr(req, "machine", None)
        or getattr(req, "environment", None)
        or getattr(req, "ahp", False)
    ):
        raise engine_client.EngineError(
            "prompt launch requires a local native New or cold Resume, without AHP"
        )
    if req.mode == "resume" and req.worktree_id:
        leg = engine_client.execution_leg_get(
            req.project, req.worktree_id,
        ).get("execution_leg")
        if leg is not None:
            if not isinstance(leg, dict):
                raise engine_client.EngineError("invalid execution leg for prompt launch")
            if leg.get("state", "unknown") in {"active", "unknown"}:
                raise engine_client.EngineError(
                    "prompt launch is unsupported for an active or uncertain execution leg"
                )
