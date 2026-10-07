"""Shared PR-branch push diagnostics and history-action rendering."""

from __future__ import annotations


def reuse_lease_expect(*records: object) -> str:
    """First non-empty ``head_sha`` among optional PR-record-like objects.

    The reuse-lease guard (#5298) for every reuse/incremental PR-branch push
    (create-pr's reuse path, its re-run fast path, and push-changes) leases
    against the branch's LAST-OBSERVED published tip -- never a live
    re-query, which would just read back whatever is there right now and
    trivially "match", defeating the guard. A mismatch between that
    remembered tip and the remote's actual current state (divergence, or
    disappearance e.g. a merge auto-pruning the branch) then fails the push
    atomically instead of silently overwriting foreign commits or
    resurrecting a deleted branch. Pass the caller's known PRRecord(s), most
    specific first; ``None`` entries (an untracked/fresh branch) are skipped.
    """
    for record in records:
        head_sha = getattr(record, "head_sha", "") if record is not None else ""
        if head_sha:
            return head_sha
    return ""


def missing_expected_sha_error(*, feature_branch: str, retry_command: str) -> str:
    """Error for a reuse push with no persisted expected SHA to lease against.

    A legacy or manually-registered (``set-pr``) record can have no
    ``head_sha`` at all. Falling back to a plain bool ``--force-with-lease``
    there would adopt whatever this call's own just-completed fetch recorded
    as the remote tip and force past it -- the same live-requery flaw the
    reuse-lease guard (#5298) exists to close, just one step removed. Refuse
    instead of guessing; ``pr-status`` genuinely repairs this (it backfills a
    missing ``head_sha`` from an identity-checked provider read in
    ``_reconcile_active_pr``), so the message points callers there.
    """
    return (
        f"No persisted expected tip is recorded for '{feature_branch}', so this "
        f"reuse push cannot safely lease against it -- falling back to a plain "
        f"force-with-lease would just re-adopt whatever the remote happens to be "
        f"right now. Run `agent-worktrees pr-status` to backfill the tracked head "
        f"from the provider, then retry {retry_command}."
    )


def pr_branch_non_fast_forward_hint(*, retry_command: str) -> str:
    """Balanced attribution for a retryable PR-branch push rejection."""
    return (
        "This could be caused either by this worktree's own earlier "
        "create-pr/push-changes rewrite of the PR branch or by another actor "
        "updating the remote branch after your last fetch/observation. "
        f"Fetch/inspect the remote PR branch, reconcile any divergent local PR "
        f"history, then re-run {retry_command}."
    )


def push_failure_detail(result: object) -> str:
    """Best-effort failure detail for PushResult-like objects."""
    detail = getattr(result, "failure_detail", "")
    if isinstance(detail, str) and detail:
        return detail
    parts = []
    stdout = str(getattr(result, "stdout", "") or "").strip()
    stderr = str(getattr(result, "stderr", "") or "").strip()
    if stdout:
        parts.append(f"git (stdout): {stdout}")
    if stderr:
        parts.append(f"git (stderr): {stderr}")
    return ("\n" + "\n".join(parts)) if parts else ""


def create_pr_history_action(
    *,
    wt_branch: str,
    upstream: str,
    rebased_onto_upstream: bool,
    reusing: bool,
    surviving_commits: int,
    squashed: bool,
) -> str:
    """Human-facing one-line summary of what create-pr actually did."""
    rewrite_lead = (
        f"Rebased '{wt_branch}' onto {upstream}"
        if rebased_onto_upstream
        else f"Preserved '{wt_branch}' without rebasing it onto newer upstream"
    )
    if reusing:
        return (
            f"{rewrite_lead} and reused the live PR head without re-squashing; "
            f"publishing the current {surviving_commits}-commit PR head "
            "incrementally."
        )
    if squashed:
        return (
            f"{rewrite_lead}, squashed {surviving_commits} surviving commit(s) "
            "into one, then published the PR head."
        )
    return (
        f"{rewrite_lead}; one surviving commit remained, so create-pr "
        "published it without additional squashing."
    )


def create_pr_push_error(
    *,
    wt_branch: str,
    feature_branch: str,
    publish_remote: str,
    reusing: bool,
    pushed: object,
    retry_command: str,
    snapshot: bool,
) -> str:
    """Render create-pr's push failure message for refspec/snapshot publish."""
    detail = push_failure_detail(pushed)
    hint = (
        "\n" + pr_branch_non_fast_forward_hint(retry_command=retry_command)
    ) if getattr(pushed, "retryable", False) else ""
    if snapshot:
        work_desc = (
            f"The current PR-head commits remain on '{wt_branch}' (and the local "
            f"'{feature_branch}' snapshot); "
            if reusing else
            f"The squashed work is on '{wt_branch}' (and the local "
            f"'{feature_branch}' snapshot); "
        )
        return (
            f"Failed to push '{feature_branch}' to '{publish_remote}'. "
            + work_desc
            + "tracking state left as 'creating' for retry (re-run create-pr)."
            + hint
            + detail
        )
    work_desc = (
        f"The current PR-head commits remain on '{wt_branch}'; "
        if reusing else
        f"The squashed work is on '{wt_branch}'; "
    )
    return (
        f"Failed to push '{wt_branch}' to '{publish_remote}/{feature_branch}'. "
        + work_desc
        + "tracking state left as 'creating' for retry (re-run create-pr)."
        + hint
        + detail
    )
