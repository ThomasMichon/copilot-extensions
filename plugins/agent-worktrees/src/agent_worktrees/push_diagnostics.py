"""Shared PR-branch push diagnostics and history-action rendering."""

from __future__ import annotations


def pr_branch_non_fast_forward_hint(*, retry_command: str) -> str:
    """Balanced attribution for a retryable PR-branch push rejection."""
    return (
        "This could be caused either by this worktree's own earlier "
        "create-pr/push-changes rewrite of the PR branch or by another actor "
        "updating the remote branch after your last fetch/observation. "
        f"Fetch/inspect the remote PR branch if needed, then re-run {retry_command}."
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
            f"publishing the current {surviving_commits}-commit PR head with "
            "--force-with-lease."
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
