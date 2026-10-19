"""Operation-specific rendering of an already-classified PR state."""

from __future__ import annotations


def operation_readiness(
    state, *, operation: str, automerge_label: str, approval_required: bool,
    mergeable: bool | None,
) -> dict:
    direct_candidate = operation == "direct"
    known_blocker = bool(
        state.merge_state in ("merged", "closed")
        or state.wip
        or state.held
        or state.conflict
        or state.verdict == "CHANGES_REQUESTED"
        or (
            state.approval_stale
            and not state.approval_stale_authorized
            and state.verdict != "APPROVED"
        )
    )
    direct_safe = bool(
        direct_candidate
        and mergeable is True
        and not known_blocker
    )
    assessment = bool(
        direct_safe and not approval_required and state.verdict == "COMMENTED"
    )
    direct_ready = bool(
        direct_safe
        and not assessment
        and (state.verdict == "APPROVED" or not approval_required)
    )
    reason = state.reason
    if direct_ready:
        reason = (
            "approved at current head; direct merge requires no consent label"
            if state.verdict == "APPROVED"
            else "eligible for direct merge; no consent label is required"
        )
    elif assessment:
        reason = "commented review requires findings assessment before direct merge"
    elif (
        direct_candidate
        and mergeable is None
        and not known_blocker
        and state.merge_state not in ("merged", "closed")
    ):
        reason = "mergeability is unknown; re-read before direct merge"
    elif direct_candidate and state.reason in (
        "no auto-merge label configured (binding absent)",
        f"{automerge_label} already present",
    ):
        reason = "not yet approved"
    return {
        "consent_action": "not-applicable" if direct_candidate else state.consent_action,
        "eligible": direct_ready if direct_candidate else state.eligible,
        "needs_consent": False if direct_candidate else state.consent_action == "apply",
        "clear_to_merge": (
            direct_ready if direct_candidate
            else state.consent_action in ("apply", "already")
        ),
        "operation": operation,
        "ready_for_direct_merge": direct_ready,
        "review_assessment_required": assessment,
        "reason": reason,
    }


__all__ = ["operation_readiness"]
