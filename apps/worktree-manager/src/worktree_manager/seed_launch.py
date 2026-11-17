"""Refuse unsupported prompt transports before the engine can stage an intent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import engine_client

if TYPE_CHECKING:
    from .picker_app import LaunchRequest


class UnsupportedSeedPrompt(engine_client.EngineError):
    """A seeded request has no supported native launch target or transport."""


def require_launch_capabilities(detail: str, *, attempt_id: str | None, staged: bool) -> None:
    if attempt_id is not None and "unrecognized arguments" in detail and any(
        flag in detail for flag in ("--require-launch-attempt", "--launch-attempt-id")
    ):
        raise engine_client.EngineFeatureUnavailable(
            "the installed engine cannot retain launch-resolution receipts; "
            "update agent-worktrees before launching this attempt"
        )
    if staged and "unrecognized arguments" in detail and any(
        flag in detail for flag in ("--stage-launch-seed", "--require-native-seed", "--launch-attempt-id")
    ):
        raise engine_client.EngineFeatureUnavailable(
            "the installed engine predates staged launch-prompt ownership "
            "with an atomic native-transport fence; "
            "update agent-worktrees before launching this prompt"
        )


def require_attempt_target(
    attempt_id: str | None, *, target_machine: str | None, seed: str | None,
) -> None:
    if attempt_id is not None and (
        not isinstance(attempt_id, str) or len(attempt_id) != 32
        or any(char not in "0123456789abcdef" for char in attempt_id)
    ):
        raise engine_client.EngineError("invalid launch attempt identity")


def require_attempt_plan(plan: dict, attempt_id: str | None) -> None:
    if attempt_id is None:
        return
    receipt = plan.get("launch_attempt_receipt")
    if (
        plan.get("launch_attempt_id") != attempt_id
        or not isinstance(receipt, dict)
        or type(receipt.get("version")) is not int or receipt["version"] != 1
        or not isinstance(receipt.get("operation_id"), str)
        or len(receipt["operation_id"]) != 32
        or any(char not in "0123456789abcdef" for char in receipt["operation_id"])
        or not isinstance(receipt.get("phase"), str)
        or receipt.get("phase") not in {"plan-ready", "forwarded", "remote-admission-returned", "unknown"}
        or set(receipt) != {"version", "operation_id", "phase"} | {
            target for source, target in (
                ("worktree_id", "worktree_id"), ("seed_id", "seed_id"),
                ("launch_request_id", "request_id"),
            ) if plan.get(source) is not None
        }
        or any(
            receipt.get(target) != plan.get(source)
            for source, target in (
                ("worktree_id", "worktree_id"), ("seed_id", "seed_id"),
                ("launch_request_id", "request_id"),
            )
        )
        or (plan.get("action") == "exec" and receipt.get("phase") != "plan-ready")
        or (plan.get("action") == "remote" and receipt.get("phase") != (
            "remote-admission-returned" if plan.get("launch_request_id") else "forwarded"
        ))
    ):
        raise engine_client.EngineFeatureUnavailable(
            "the engine omitted a matching launch-resolution receipt; "
            "inspect the saved attempt before retrying"
        )


def require_remote_seed_plan(
    plan: dict, *, new: bool, worktree_id: str | None, attempt_id: str | None,
) -> None:
    if (
        plan.get("action") != "remote" or type(plan.get("launch_request_version")) is not int
        or plan["launch_request_version"] != 2 or plan.get("native_seed_required") is not True
        or "launch_attempt_id" not in plan or plan["launch_attempt_id"] != attempt_id
        or plan.get("seed_pending") is not True
        or not isinstance(plan.get("launch_request_id"), str)
        or len(plan["launch_request_id"]) != 32
        or any(char not in "0123456789abcdef" for char in plan["launch_request_id"])
        or plan.get("seed_id") != plan["launch_request_id"]
        or plan.get("seed_kind") != ("new" if new else "resume")
        or not isinstance(plan.get("worktree_id"), str) or not plan["worktree_id"]
        or (worktree_id is not None and plan["worktree_id"] != worktree_id)
    ):
        raise engine_client.EngineFeatureUnavailable(
            "the engine omitted negotiated remote prompt ownership or attempt correlation; "
            "inspect the target admission before resubmitting"
        )


def require_supported_seed(req: LaunchRequest) -> None:
    if not getattr(req, "seed_prompt", None):
        return
    if (
        req.mode not in {"new", "resume"}
        or (getattr(req, "environment", None) and not getattr(req, "machine", None))
        or getattr(req, "ahp", False)
    ):
        raise UnsupportedSeedPrompt(
            "prompt launch requires a native New or cold Resume with an explicit target, without AHP"
        )
    if req.mode == "resume":
        worktree_id = getattr(req, "worktree_id", None)
        if not worktree_id:
            raise UnsupportedSeedPrompt("prompted Resume requires a worktree id")
        if getattr(req, "machine", None):
            return  # Only the selected target can inspect its authoritative execution leg.
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
