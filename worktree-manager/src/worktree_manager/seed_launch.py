"""Refuse unsupported prompt transports before the engine can stage an intent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import engine_client

if TYPE_CHECKING:
    from .picker_app import LaunchRequest


class UnsupportedSeedPrompt(engine_client.EngineError):
    """A seeded request has no supported native launch target or transport."""


def require_supported_seed(req: LaunchRequest) -> None:
    if not getattr(req, "seed_prompt", None):
        return
    if (
        req.mode not in {"new", "resume"}
        or getattr(req, "machine", None)
        or getattr(req, "environment", None)
        or getattr(req, "ahp", False)
    ):
        raise UnsupportedSeedPrompt(
            "prompt launch requires a local native New or cold Resume, without AHP"
        )
    if req.mode == "resume":
        worktree_id = getattr(req, "worktree_id", None)
        if not worktree_id:
            raise UnsupportedSeedPrompt("prompted Resume requires a worktree id")
        leg = engine_client.execution_leg_get(
            req.project, worktree_id,
        ).get("execution_leg")
        if leg is not None:
            if not isinstance(leg, dict):
                raise UnsupportedSeedPrompt("invalid execution leg for prompt launch")
            if leg.get("state", "unknown") in {"active", "unknown"}:
                raise UnsupportedSeedPrompt(
                    "prompt launch is unsupported for an active or uncertain execution leg"
                )
