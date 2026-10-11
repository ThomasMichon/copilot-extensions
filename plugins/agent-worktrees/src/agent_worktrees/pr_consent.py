"""Actionable consent updates, separate from provider review transitions."""

from __future__ import annotations

import hashlib
import json
import shlex

from . import pr_contract as pc


def consent_command(repo: str, number: int) -> str:
    """Name the authorized single-PR path, never a sweep or direct label edit."""
    return f"agent-worktrees pr-merge {shlex.quote(repo)} {number}"


def consent_argv(repo: str, number: int) -> list[str]:
    """Shell-independent follow-up for structured consumers."""
    return ["agent-worktrees", "pr-merge", repo, str(number)]


def consent_update_key(
    snap: pc.PRSnapshot,
    readiness: dict,
    *,
    flow_profile: str,
    default_branch: str = "",
) -> str:
    """Identify a current, unblocked approval needing configured author consent."""
    if (
        flow_profile != pc.PROFILE_PR_AGENT_MERGE
        or not readiness.get("needs_consent")
        or readiness.get("verdict") != "APPROVED"
        or readiness.get("approval_stale")
        or not snap.head_sha
        or not snap.author
        or snap.mergeable is not True
        or snap.checks_state not in ("none", "success")
        or (default_branch and snap.base_ref != default_branch)
    ):
        return ""
    approval = max(
        (
            (r.id, r.user, r.commit_id)
            for r in snap.reviews
            if r.user and r.user != snap.author and not r.dismissed
            and r.state.upper() == "APPROVED"
            and r.commit_id == snap.head_sha
        ),
        default=None,
    )
    if approval is None:
        return ""
    evidence = [
        snap.head_sha,
        readiness["consent_label"].lower(),
        approval,
    ]
    return hashlib.sha256(json.dumps(evidence).encode("utf-8")).hexdigest()[:24]
