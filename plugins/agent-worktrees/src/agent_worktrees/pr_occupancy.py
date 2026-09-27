"""Next-actor occupancy for a PR snapshot.

Callers recompute from live head and verdict commit; they must not reuse
a prior wait reason. A leftover APPROVED review of an older SHA is
needs-review, never needs-consent.
"""

from __future__ import annotations


def occupancy_for(
    *,
    wip: bool,
    conflict: bool,
    verdict: str,
    approval_stale: bool,
    approval_stale_authorized: bool,
    consent_present: bool,
    consent_action: str = "",
    merge_state: str = "",
) -> str:
    """Who must act next (needs-review / needs-consent / needs-merge / ...)."""
    if merge_state == "merged":
        return "merged"
    if merge_state == "closed":
        return "closed"
    if wip:
        return "wip"
    if conflict:
        return "author-conflict"
    if approval_stale and not approval_stale_authorized:
        return "needs-review"
    if verdict == "CHANGES_REQUESTED":
        return "needs-revision"
    if verdict == "APPROVED":
        if consent_present:
            return "needs-merge"
        return "needs-consent"
    if consent_action == "already":
        return "needs-merge"
    if consent_action == "apply":
        return "needs-consent"
    return "needs-review"


def occupancy_from_state(state) -> str:
    """Occupancy from a :class:`~agent_worktrees.pr_contract.PRState`."""
    return occupancy_for(
        wip=bool(state.wip),
        conflict=bool(state.conflict),
        verdict=str(state.verdict or ""),
        approval_stale=bool(state.approval_stale),
        approval_stale_authorized=bool(state.approval_stale_authorized),
        consent_present=bool(state.consent_present),
        consent_action=str(getattr(state, "consent_action", "") or ""),
        merge_state=str(getattr(state, "merge_state", "") or ""),
    )


def occupancy_from_readiness(readiness: dict) -> dict:
    """Copy ``readiness`` and set ``occupancy`` from its classification fields."""
    out = dict(readiness)
    out["occupancy"] = occupancy_for(
        wip=bool(readiness.get("wip")),
        conflict=bool(readiness.get("conflict")),
        verdict=str(readiness.get("verdict") or ""),
        approval_stale=bool(readiness.get("approval_stale")),
        approval_stale_authorized=bool(
            readiness.get("approval_stale_authorized")
        ),
        consent_present=bool(readiness.get("consent_present")),
        consent_action=str(readiness.get("consent_action") or ""),
        merge_state=str(readiness.get("merge_state") or ""),
    )
    return out
