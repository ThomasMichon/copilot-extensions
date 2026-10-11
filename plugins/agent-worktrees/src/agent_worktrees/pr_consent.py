"""Actionable consent updates, separate from provider review transitions."""

from __future__ import annotations

import hashlib
import json
import shlex

from . import pr_contract as pc


def consent_command(repo: str, number: int) -> str:
    """Name the authorized single-PR path, never a sweep or direct label edit."""
    return f"agent-worktrees pr-merge {shlex.quote(repo)} {number}"


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
        or snap.mergeable is not True
        or snap.checks_state not in ("", "success")
        or (default_branch and snap.base_ref != default_branch)
    ):
        return ""
    evidence = [
        snap.head_sha,
        readiness["consent_label"].lower(),
        sorted(
            (r.id, r.state, r.user, r.commit_id, r.dismissed)
            for r in snap.reviews if r.user != snap.author
        ),
    ]
    return hashlib.sha256(json.dumps(evidence).encode("utf-8")).hexdigest()[:24]
