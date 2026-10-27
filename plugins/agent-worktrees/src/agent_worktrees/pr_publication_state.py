"""Fresh publication RMWs that preserve concurrent non-authority stamps."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta
import json

from . import config as cfg, tracking


def authority(pr: tracking.PRRecord) -> tuple:
    return tuple(getattr(pr, name) for name in (
        "branch", "repo", "provider", "number", "url", "state",
        "base_sha", "head_sha", "patch_id", "remote", "head_repo",
        "head_identity", "head_owner", "rewrite_owner", "rewrite_identity",
    )) + (tuple(sorted(pr.creation.items())), json.dumps(pr.publication_origin, sort_keys=True))


def loaded(record: tracking.WorktreeRecord) -> tracking.WorktreeRecord:
    for pr in record.prs:
        pr._loaded_authority = authority(pr)
    return record


def same_loaded_authority(current: tracking.PRRecord, incoming: tracking.PRRecord) -> bool:
    return incoming._loaded_authority is not None and authority(current) == incoming._loaded_authority


def matches(current: tracking.PRRecord | None, expected: tracking.PRRecord) -> bool:
    return current is not None and not tracking._pr_is_terminal(current) and authority(current) == authority(expected)


def copy_pr(destination: tracking.PRRecord, source: tracking.PRRecord) -> None:
    for field in fields(source):
        if not field.name.startswith("_"):
            setattr(destination, field.name, getattr(source, field.name))


def advance_authority_revisions(record: tracking.WorktreeRecord, current: tracking.WorktreeRecord) -> None:
    from . import pr_publish

    for pr in record.prs:
        previous = pr_publish._publication_pr(current, pr)
        if previous is not None and authority(previous) != authority(pr) and pr.pr_revision <= previous.pr_revision:
            pr.pr_revision = previous.pr_revision + 1


def require_current(record: tracking.WorktreeRecord, expected: tracking.PRRecord | None) -> None:
    from . import pr_publish

    if expected is None:
        raise ValueError("No live tracked PR remains; reload the publication target before retrying.")
    with tracking._RecordLock(record.yaml_path, require_sidecar=True):
        fresh = tracking.load_record(record.yaml_path)
        if (fresh.worktree_id, fresh.repo, fresh.branch, fresh.worktree_path) != (
                record.worktree_id, record.repo, record.branch, record.worktree_path):
            raise ValueError("Owning worktree identity changed before publication; reload it before retrying.")
        if not matches(pr_publish._publication_pr(fresh, expected), expected):
            raise ValueError("Tracked PR authority changed before publication; reload it before retrying.")


def persist_pull(config, record, target, pull, *, head_sha: str, marker_published: bool, session: str):
    from . import pr_ops, pr_publish

    path = cfg.tracking_dir(config.repo_name) / f"{record.worktree_id}.yaml"
    with pr_publish.metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(path, require_sidecar=True):
        fresh = tracking.load_record(path)
        current = pr_publish._publication_pr(fresh, target)
        if not matches(current, target):
            raise ValueError("Provider PR opened, but tracking authority changed; reconcile the provider identity.")
        current.url = pull.url
        current.number = pull.number
        current.state = pull.state or current.state
        current.pr_revision += 1
        if marker_published:
            current.attribution_head = head_sha
        if not fresh.parent_session and session:
            fresh.parent_session = session
        claimed_ref = pr_ops._ensure_pr_claim(fresh, current)
        tracking.save_record(fresh)
        copy_pr(target, current)
        record.resources = fresh.resources
        record.parent_session = fresh.parent_session
        return claimed_ref


def provisional_lease(pr, remote: str, branch: str, cwd: str) -> str | None:
    """An unleased first attempt may only create an absent ref, never adopt one."""
    from . import pr_publish

    if pr is None or pr.state != "creating" or pr.head_sha or pr.number is not None or pr.url or pr.rewrite_owner:
        return None
    tip = pr_publish._tip(remote, branch, cwd)
    if tip is None:
        raise ValueError("Cannot inspect the unleased provisional PR head; retry after restoring remote access.")
    if tip:
        raise ValueError("Unleased provisional PR head already exists; inspect and reconcile the ambiguous push before retrying.")
    return ""


def unpublished(pr: tracking.PRRecord | None) -> bool:
    """Identify a provisional entry with no publication or provider evidence."""
    # head_sha is the saved lease; remote/head/rewrite identities bind destination.
    return pr is not None and pr.state == "creating" and pr.number is None and not any(
        (
            pr.url, pr.base_sha, pr.head_sha, pr.patch_id, pr.rewrite_owner,
            pr.rewrite_identity, pr.remote, pr.head_repo, pr.head_identity, pr.head_owner,
            pr.head_observed_at, pr.head_observed_api_base, pr.attribution_head, pr.closed_at,
        )
    )


def unpublished_retry(
    pr: tracking.PRRecord | None, branch: str, provider: str, repo: str,
) -> bool:
    """Match a never-published attempt without borrowing another destination."""
    if unpublished(pr) and pr.branch == branch and (pr.provider, pr.repo) != (provider, repo):
        raise ValueError("Unpublished PR destination changed; select a distinct head before retrying.")
    return bool(
        unpublished(pr) and pr is not None
        and (pr.branch, pr.provider, pr.repo) == (branch, provider, repo)
    )


def retry_candidate(
    record: tracking.WorktreeRecord | None, active: tracking.PRRecord | None,
    branch: str, provider: str, repo: str,
) -> tracking.PRRecord | None:
    """Find one matching attempt across interleaved heads without merging identities."""
    candidates = [
        pr for pr in record.prs
        if not tracking._pr_is_terminal(pr) and pr.branch == branch
    ] if record is not None else []
    if len(candidates) > 1:
        raise ValueError("Multiple live PR associations share this head; reconcile them before retrying.")
    if candidates and unpublished_retry(candidates[0], branch, provider, repo):
        return candidates[0]
    return active


def monotonic_opened_at(record: tracking.WorktreeRecord) -> str:
    """Return an ``opened_at`` guaranteed later than every PR already on this record.

    ``_now_iso()`` has whole-second resolution, so two record mutations in the
    same tick would otherwise tie (or, once one of them is bumped past that
    resolution, sort *before* a genuinely later sibling). Advance past the
    current max by one microsecond when real time hasn't caught up to it yet.
    Shared by every ``opened_at`` assignment -- provisional allocation and
    settlement alike -- so no lifecycle stage can issue a timestamp that is
    out of order relative to its own record's siblings.
    """
    latest = max((pr.opened_at or "" for pr in record.prs), default="")
    now = tracking._now_iso()
    return (
        (datetime.fromisoformat(latest) + timedelta(microseconds=1)).isoformat()
        if latest and latest >= now else now
    )


def settle_creation(record: tracking.WorktreeRecord, target: tracking.PRRecord) -> None:
    """Make a successfully published attempt the default without changing its peers.

    Applies the same monotonic-ordering policy as :func:`monotonic_opened_at`
    to every settle, fresh or resumed, so whichever PR actually publishes last
    always sorts last.
    """
    target.state = "open"
    target.opened_at = monotonic_opened_at(record)
