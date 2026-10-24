"""Manager boundary evidence, not engine receipts or permission to retry."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .launch_attempts import LaunchAttemptError, LaunchAttempts

log = logging.getLogger(__name__)


def observe(
    attempt_id: str | None, phase: str, *, required: bool = True, **details: str | int,
) -> None:
    if attempt_id is None:
        return  # Unjournaled CLI callers do not manufacture a confirmed intent.
    try:
        LaunchAttempts().append(attempt_id, owner="manager", phase=phase, **details)
    except LaunchAttemptError:
        if required:
            raise
        log.warning("Could not retain launch outcome; inspect the saved attempt. Do not retry automatically.")


def run_attempt(attempt_id: str | None, work: Callable[[], int]) -> int:
    """Fail closed before effects; never disguise a completed effect as a retry."""
    try:
        return work()
    except LaunchAttemptError:
        print("error: could not retain launch observation; no further launch step was authorized.")
        return 1
    except (Exception, KeyboardInterrupt) as error:
        observe(
            attempt_id, "unknown", required=False,
            code="interrupted" if isinstance(error, KeyboardInterrupt) else "engine-error",
        )
        raise


def refuse(attempt_id: str | None, code: str = "unsupported") -> int:
    observe(attempt_id, "refused", code=code)
    return 1


def launch(attempt_id: str | None, work: Callable[[], int]) -> int:
    observe(attempt_id, "launch-requested")
    return returned(attempt_id, work())


def returned(attempt_id: str | None, result: int) -> int:
    observe(attempt_id, "launcher-returned", required=False, exit_code=result)
    return result


def embody(
    attempt_id: str | None, work: Callable[[], tuple[bool, dict | str]],
) -> tuple[bool, dict | str]:
    try:
        observe(attempt_id, "embodiment-requested")
    except LaunchAttemptError:
        return False, "could not retain embodiment observation; no embodiment started"
    try:
        ok, payload = work()
    except Exception:
        observe(attempt_id, "unknown", required=False, code="engine-error")
        raise
    if ok and isinstance(payload, dict):
        observe(
            attempt_id, "embodiment-returned", required=False,
            worktree_id=payload["worktree"],
        )
    else:
        observe(attempt_id, "unknown", required=False, code="engine-error")
    return ok, payload


def resolve_for(req, *, defer_projections: bool = False) -> tuple[object | None, int]:
    from . import engine_client
    from .seed_launch import require_supported_seed

    attempt_id = getattr(req, "attempt_id", None)
    try:
        try:
            require_supported_seed(req)
        except engine_client.EngineError:
            observe(attempt_id, "refused", code="unsupported")
            raise
        # Resolution may allocate before returning or failing. Commit the
        # boundary before entering it, never label an ambiguous failure refused.
        observe(attempt_id, "resolution-requested")
        try:
            plan = engine_client.resolve_launch_plan(
                req.project, worktree_id=req.worktree_id,
                new=req.mode == "new", bare_resume=req.mode == "bare-resume",
                base=req.mode == "base",
                target_machine=getattr(req, "machine", None),
                target_environment=getattr(req, "environment", None),
                target_no_mux=getattr(req, "no_mux", False),
                seed=getattr(req, "seed_prompt", None),
                **({"launch_attempt_id": attempt_id} if attempt_id is not None else {}),
                **({"defer_projections": True} if defer_projections else {}),
            )
        except engine_client.EngineError:
            observe(attempt_id, "unknown", required=False, code="engine-error")
            raise
        details = {}
        if getattr(plan, "worktree_id", None) is not None:
            details["worktree_id"] = plan.worktree_id
        raw = getattr(plan, "raw", {})
        for source, target in (("launch_request_id", "request_id"), ("seed_id", "seed_id")):
            if source in raw and raw[source] is not None:
                details[target] = raw[source]
        observe(attempt_id, "resolved", **details)
        return plan, 0
    except LaunchAttemptError:
        print("error: could not retain launch resolution; inspect the saved attempt before retrying.")
    except engine_client.EngineError as error:
        print(f"error: could not resolve a launch plan: {error}")
    return None, 1
