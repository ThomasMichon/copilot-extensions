"""Fresh publication RMWs that preserve concurrent non-authority stamps."""

from __future__ import annotations

from dataclasses import fields

from . import config as cfg, tracking


def authority(pr: tracking.PRRecord) -> tuple:
    return tuple(getattr(pr, name) for name in (
        "pr_id", "branch", "repo", "provider", "number", "url", "state",
        "base_sha", "head_sha", "patch_id", "remote", "head_repo",
        "head_identity", "head_owner", "rewrite_owner", "rewrite_identity",
    ))


def matches(current: tracking.PRRecord | None, expected: tracking.PRRecord) -> bool:
    return current is not None and not tracking._pr_is_terminal(current) and authority(current) == authority(expected)


def copy_pr(destination: tracking.PRRecord, source: tracking.PRRecord) -> None:
    for field in fields(source):
        setattr(destination, field.name, getattr(source, field.name))


def require_current(record: tracking.WorktreeRecord, expected: tracking.PRRecord | None) -> None:
    from . import pr_publish

    if expected is None:
        raise ValueError("No live tracked PR remains; reload the publication target before retrying.")
    with tracking._RecordLock(record.yaml_path, require_sidecar=True):
        fresh = tracking.load_record(record.yaml_path)
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
