"""Read-only planning and last-moment validation of exact PR recovery actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json
import re

from . import git_ops, output, pr_break_glass, pr_publication_state, pr_publish, pr_recovery, tracking
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


def _evidence_snapshot(pr: tracking.PRRecord, cwd: str) -> tuple[str, bool]:
    """Bind imperfect bytes and all referenced refs, not just a first failure."""
    path = pr_recovery._path(cwd)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "", False
    except OSError as exc:
        snapshot: dict[str, object] = {"read_error": type(exc).__name__, "errno": exc.errno}
        try:
            info = path.stat()
            snapshot["stat"] = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_ino)
        except OSError as stat_error:
            snapshot["stat_error"] = (type(stat_error).__name__, stat_error.errno)
        return _fingerprint(snapshot), True
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    point = None
    if isinstance(data, dict):
        points = data.get("checkpoints")
        point = points.get(pr.pr_id) if isinstance(points, dict) else data
    refs = {}
    unusable = False
    if isinstance(point, dict):
        for field in ("local_ref", "published_ref", "lineage_ref"):
            ref = point.get(field)
            if isinstance(ref, str) and ref:
                try:
                    result = git_ops.git(
                        "--no-replace-objects", "rev-parse", "--verify", "--end-of-options",
                        ref, cwd=cwd, check=False, isolated_repository=True,
                    )
                except (ValueError, OSError, git_ops.GitError) as exc:
                    unusable = True
                    refs[field] = {"probe_error": type(exc).__name__, "errno": getattr(exc, "errno", None)}
                else:
                    refs[field] = (result.returncode, result.stdout.strip())
    return _fingerprint({"bytes": hashlib.sha256(raw).hexdigest(), "refs": refs}), unusable


def build(
    config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    *, source_head: str, expected_head: str, identity: str, overrides: tuple[str, ...],
    cwd: str,
) -> pr_break_glass.RecoveryPreview:
    point = None
    evidence = ""
    unusable = False
    if expected_head != pr.head_sha:
        evidence, unusable = _evidence_snapshot(pr, cwd)
        try:
            point = pr_recovery.synced(record, pr, pr.head_sha, source_head, cwd=cwd)
        except (ValueError, OSError, git_ops.GitError):
            unusable = True
        if point is not None and not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", point.target_head):
            unusable = True
        if unusable:
            point = None
            output.warn(
                "Recovery settlement evidence is unusable; the preview requires explicit rewrite sign-off. "
                "Historical evidence is preserved, not repaired or treated as publication authority."
            )
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
        evidence_fingerprint=_fingerprint((evidence, asdict(point))) if point is not None else evidence,
        evidence_status="unusable" if unusable else "backed-sync" if point is not None else "none",
        rebase_base_sha=point.target_head if point is not None else "",
        overrides=overrides,
    )


def revalidate(
    config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    operation: pr_break_glass.RecoveryPreview, *, remote: str,
    observe: Callable[[Config, tracking.PRRecord], PullResult], cwd: str,
    allow_feature_head: bool = False,
) -> None:
    """Refuse changed input after retention; transport still CASes the exact ref."""
    pr_publication_state.require_current(record, pr)
    # A legacy record may store the worktree branch as the bare worktree id
    # instead of 'worktree/<id>' -- pr_rebase.verify() already normalizes
    # this form; recognize the same equivalence here.
    owning_branch = (
        f"worktree/{record.worktree_id}" if record.branch == record.worktree_id else record.branch
    )
    current_branch = git_ops._get_current_branch_safe(cwd)
    on_owning_head = current_branch == owning_branch or (allow_feature_head and current_branch == pr.branch)
    if not git_ops.is_clean(cwd=cwd) or not on_owning_head:
        raise ValueError("Recovery source branch or working tree changed before publication.")
    source = git_ops.git(
        "--no-replace-objects", "rev-parse", "--verify", "HEAD",
        cwd=cwd, isolated_repository=True,
    ).stdout.strip()
    target = pr_publish.push_target(config.default_repo, pr, cwd)
    if target is None or target.remote != remote:
        raise ValueError("Recovery publication destination changed before publication.")
    _, _, destination = pr_publish._remote_snapshot(remote, cwd=cwd)
    identity = destination.head_identity
    if not identity or not destination.head_repo:
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
