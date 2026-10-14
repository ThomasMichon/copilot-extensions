"""Read-only planning and last-moment validation of exact PR recovery actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json

from . import git_ops, pr_break_glass, pr_publication_state, pr_publish, pr_recovery, tracking
from .config import Config
from .providers.base import PullResult
from .providers.pr_source import same_repository


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def saved_publication(pr: tracking.PRRecord) -> dict[str, object]:
    """Keep original bookkeeping evidence without credential-bearing URLs."""
    return {name: getattr(pr, name) for name in (
        "pr_id", "pr_revision", "provider", "repo", "number", "branch",
        "state", "base_sha", "head_sha", "patch_id",
    )}


def build(
    config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    *, source_head: str, expected_head: str, identity: str, overrides: tuple[str, ...],
    cwd: str,
) -> pr_break_glass.RecoveryPreview:
    point = None
    if expected_head != pr.head_sha:
        point = pr_recovery.synced(record, pr, pr.head_sha, source_head, cwd=cwd)
        if point is not None and point.synced_head != expected_head:
            point = None
    if pr.number is None:
        raise ValueError("Recovery requires a numbered existing PR.")
    return pr_break_glass.RecoveryPreview(
        provider=pr.provider or config.default_repo.pr.provider,
        repo=pr.repo, number=pr.number, branch=pr.branch,
        destination_fingerprint=_fingerprint(identity),
        source_head=source_head, expected_head=expected_head, saved_head=pr.head_sha,
        publication_fingerprint=_fingerprint((
            record.worktree_id, record.repo, record.worktree_path, record.branch,
            pr.pr_id, pr_publication_state.authority(pr), config.default_repo.pr.api_base,
        )),
        action="settle-publication" if point is not None else "rewrite",
        evidence_fingerprint=_fingerprint(asdict(point)) if point is not None else "",
        overrides=overrides,
    )


def revalidate(
    config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    operation: pr_break_glass.RecoveryPreview, *, remote: str,
    observe: Callable[[Config, tracking.PRRecord], PullResult], cwd: str,
) -> None:
    """Refuse changed input after retention; transport still CASes the exact ref."""
    pr_publication_state.require_current(record, pr)
    if not git_ops.is_clean(cwd=cwd) or git_ops._get_current_branch_safe(cwd) != record.branch:
        raise ValueError("Recovery source branch or working tree changed before publication.")
    source = git_ops.git(
        "--no-replace-objects", "rev-parse", "--verify", f"refs/heads/{record.branch}",
        cwd=cwd, isolated_repository=True,
    ).stdout.strip()
    target = pr_publish.push_target(config.default_repo, pr, cwd)
    if target is None or target.remote != remote:
        raise ValueError("Recovery publication destination changed before publication.")
    identity = pr_publish.push_identity(remote, cwd=cwd)
    if not identity or not pr_publish.push_slug(remote, cwd=cwd):
        raise ValueError("Recovery destination is no longer readable.")
    observed = observe(config, pr)
    tip = pr_publish._tip(remote, pr.branch, cwd)
    if (
        observed.merged or observed.state.lower() != "open" or observed.number != pr.number
        or observed.head_ref != pr.branch or not observed.head_url or not tip
        or observed.head_sha != tip
        or not same_repository(
            pr_publish._identity(observed.head_url, cwd), identity, operation.provider,
        )
    ):
        raise ValueError("Provider and remote no longer agree on the exact recovery PR head.")
    current = build(
        config, record, pr, source_head=source, expected_head=tip,
        identity=identity, overrides=operation.overrides, cwd=cwd,
    )
    if current != operation:
        raise ValueError("Recovery plan changed before publication; obtain a fresh preview and sign-off.")
