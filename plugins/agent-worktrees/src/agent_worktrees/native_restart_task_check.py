"""Read current task policy from its registered owner, never infer a writer."""

from __future__ import annotations

from . import claim_providers, native_restart_claims, tracking


_FIELDS = {
    "schema", "version", "task_id", "repo", "state", "owner", "owner_session_id", "generation",
    "awaiting_steer", "resume_requested", "hold_reason", "hold_actor", "hold_at", "run_waiter",
    "pending_steer",
    "task_qualified", "launch_authorized", "automatic_eligible", "reason",
}
_NAMESPACES = {"dispatch-local-task", "dispatch-central-task"}


def check(record: tracking.WorktreeRecord, expected: dict) -> tuple[str | None, list[dict]]:
    """Validate one explicit current task snapshot; all other claims stay blocked.

    Dispatch-attempt metadata is historical provenance, not current ownership.
    A positive generation is retained for a future action-time fence; this
    read does not reserve it or prove that it survives until a future launch.
    """
    if record.dispatch_attempt_opaque or (
        record.dispatch_attempt_raw_present and record.dispatch_attempt is None
    ):
        return "task-policy-unqualified", []
    if record.owner_ref or record.last_finalize_released:
        return "claim-policy-unqualified", []
    if native_restart_claims.original_session_only(record, expected["native_session_id"]):
        return None, []
    if not record.resources:
        return ("task-policy-unqualified" if record.dispatch_attempt is not None else None), []
    # One bounded callback. Mixed claims, producer claims and historical/released
    # claims need their own admission protocol rather than a status-based guess.
    if len(record.resources) != 1:
        return "claim-policy-unqualified", []
    claim = record.resources[0]
    if claim.kind != "task" or claim.state != "active" or claim.has_producer or claim.metadata_opaque:
        return "claim-policy-unqualified", []
    split = claim_providers.split_namespaced_ref(claim.ref)
    if (
        split is None or split[0] not in _NAMESPACES
        or claim.ref != f"{split[0]}:{split[1]}" or len(split[1]) > 256
        or record.dispatch_attempt is not None and record.dispatch_attempt.task_id != split[1]
    ):
        return "task-policy-unqualified", []
    response = claim_providers.resolve_claim_status(
        claim.ref, native_restart_policy=True, timeout=2,
    )
    if response.get("available") is not True or response.get("exists") is not True:
        return "task-policy-unavailable", []
    policy = response.get("native_restart_policy")
    if not isinstance(policy, dict) or set(policy) != _FIELDS:
        return "task-policy-unqualified", []
    if (
        policy["schema"] != "native-restart-task-policy"
        or type(policy["version"]) is not int or policy["version"] != 1
        or policy["task_id"] != split[1] or policy["repo"] != record.repo
        or policy["owner"] != f"{record.machine}/{record.worktree_id}"
        or policy["owner_session_id"] != expected["native_session_id"]
        or policy["state"] != "started"
        or type(policy["generation"]) is not int or not 1 <= policy["generation"] <= 2**63 - 1
        or any(policy[key] is not False for key in (
            "awaiting_steer", "pending_steer", "resume_requested", "run_waiter",
            "launch_authorized", "automatic_eligible",
        ))
        or any(policy[key] is not None for key in ("hold_reason", "hold_actor", "hold_at"))
        or policy["task_qualified"] is not True or policy["reason"] != "current-task-matches"
    ):
        return "task-policy-unqualified", []
    return None, [{"ref": claim.ref, "policy": policy}]
