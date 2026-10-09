"""Durable, worktree-scoped recovery points for intentional synchronization."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import git_ops, tracking


@dataclass(frozen=True)
class RecoveryPoint:
    version: int
    worktree_id: str
    branch: str
    local_head: str
    local_ref: str
    target_head: str
    published_head: str = ""
    published_ref: str = ""
    pr_id: str = ""
    pr_branch: str = ""
    pr_repo: str = ""
    pr_provider: str = ""
    association: str = ""
    lineage_head: str = ""
    lineage_ref: str = ""
    synced_head: str = ""


def _rev(ref: str, cwd: str) -> str:
    return git_ops.git(
        "--no-replace-objects", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}",
        cwd=cwd, isolated_repository=True,
    ).stdout.strip()


def _path(cwd: str) -> Path:
    gitdir = git_ops.git(
        "rev-parse", "--absolute-git-dir", cwd=cwd, isolated_repository=True,
    ).stdout.strip()
    return Path(gitdir) / "agent-worktrees-pr-recovery.json"


def _association(pr) -> str:
    fields = (
        pr.pr_id, pr.provider, pr.repo, pr.number, pr.url, pr.branch,
        pr.remote, pr.head_repo, pr.head_identity, pr.head_owner,
    )
    return hashlib.sha256(json.dumps(fields).encode("utf-8")).hexdigest()


def _pending_path(cwd: str) -> Path:
    return _path(cwd).with_name("agent-worktrees-pr-recovery-pending.json")


def _points(cwd: str) -> dict[str, dict]:
    path = _path(cwd)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        raise ValueError("Invalid PR recovery checkpoint")
    if data["version"] == 1:
        if not isinstance(data.get("pr_id"), str):
            raise ValueError("Invalid PR recovery checkpoint")
        return {data["pr_id"]: data}
    points = data.get("checkpoints")
    if (
        data["version"] != 2 or not isinstance(points, dict)
        or not all(isinstance(value, dict) for value in points.values())
        or not isinstance(data.get("latest"), str) or data["latest"] not in points
    ):
        raise ValueError("Invalid PR recovery checkpoint collection")
    return points


def selected_pr(worktree_id: str, branch: str, record):
    if record is None:
        return None
    if branch == f"worktree/{worktree_id}":
        active = record.active_pr()
        return active if active is not None and not tracking._pr_is_terminal(active) else None
    matches = [
        pr for pr in record.prs if pr.branch == branch and not tracking._pr_is_terminal(pr)
    ]
    if len(matches) > 1:
        raise ValueError("The checked-out private head matches multiple live PR records")
    return matches[0] if matches else None


def prepare(worktree_id: str, branch: str, target: str, record, *, cwd: str) -> RecoveryPoint:
    """Retain committed local and recorded published tips before changing HEAD."""
    local = _rev("HEAD", cwd)
    onto = _rev(target, cwd)
    pr = selected_pr(worktree_id, branch, record)
    published = _rev(pr.head_sha, cwd) if pr is not None and pr.head_sha else ""
    lineage = local
    if published and not git_ops.is_commit_ancestor(published, local, cwd=cwd):
        previous = synced(record, pr, published, local, cwd=cwd)
        lineage = previous.lineage_head if previous is not None else ""
    namespace = hashlib.sha256(worktree_id.encode("utf-8")).hexdigest()[:24]
    root = f"refs/agent-worktrees/recovery/{namespace}/{uuid.uuid4().hex}"
    point = RecoveryPoint(
        version=1, worktree_id=worktree_id, branch=branch, local_head=local,
        local_ref=f"{root}/local", target_head=onto, published_head=published,
        published_ref=f"{root}/published" if published else "",
        pr_id=pr.pr_id if pr is not None else "",
        pr_branch=pr.branch if pr is not None else "",
        pr_repo=pr.repo if pr is not None else "",
        pr_provider=pr.provider if pr is not None else "",
        association=_association(pr) if pr is not None else "",
        lineage_head=lineage, lineage_ref=f"{root}/lineage" if lineage else "",
    )
    git_ops.git(
        "update-ref", point.local_ref, local, "0" * len(local),
        cwd=cwd, isolated_repository=True,
    )
    if published:
        git_ops.git(
            "update-ref", point.published_ref, published, "0" * len(published),
            cwd=cwd, isolated_repository=True,
        )
    if lineage:
        git_ops.git(
            "update-ref", point.lineage_ref, lineage, "0" * len(lineage),
            cwd=cwd, isolated_repository=True,
        )
    tracking._atomic_write(_pending_path(cwd), json.dumps(asdict(point), sort_keys=True) + "\n")
    if published and not lineage:
        raise ValueError(
            "Pre-sync source does not contain the saved published work or a completed backed sync. "
            f"Original HEAD is retained at {point.local_ref}; "
            f"revisit it with git switch --detach {point.local_head}. "
            "An arbitrary rewrite requires the explicit owned-PR rewrite flow."
        )
    return point


def complete(point: RecoveryPoint, *, cwd: str) -> None:
    """Bind the supported operation's result without depending on Git's reflog."""
    if point.pr_id and point.published_head:
        result = replace(point, synced_head=_rev("HEAD", cwd))
        points = _points(cwd)
        points[point.pr_id] = asdict(result)
        collection = {"version": 2, "latest": point.pr_id, "checkpoints": points}
        tracking._atomic_write(_path(cwd), json.dumps(collection, sort_keys=True) + "\n")
    _pending_path(cwd).unlink(missing_ok=True)


def synced(record, pr, expected: str, head: str, *, cwd: str) -> RecoveryPoint | None:
    """A saved sync authorizes only its original association and descendant tip."""
    data = _points(cwd).get(pr.pr_id)
    if data is None:
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Invalid PR recovery checkpoint")
    try:
        point = RecoveryPoint(**data)
    except TypeError as exc:
        raise ValueError("Invalid PR recovery checkpoint") from exc
    if not all(isinstance(value, str) for key, value in asdict(point).items() if key != "version"):
        raise ValueError("Invalid PR recovery checkpoint fields")
    if not point.synced_head or not point.published_head:
        return None
    if (
        point.worktree_id != record.worktree_id
        or point.branch not in (f"worktree/{record.worktree_id}", pr.branch)
        or (point.pr_id, point.pr_branch, point.pr_repo, point.pr_provider)
        != (pr.pr_id, pr.branch, pr.repo, pr.provider)
        or point.association != _association(pr)
        or point.published_head != expected
        or not point.lineage_head or not point.lineage_ref
        or not git_ops.is_commit_ancestor(expected, point.lineage_head, cwd=cwd)
        or not git_ops.is_commit_ancestor(point.synced_head, head, cwd=cwd)
        or not git_ops.is_commit_ancestor(point.target_head, point.synced_head, cwd=cwd)
    ):
        return None
    if (
        _rev(point.local_ref, cwd) != point.local_head
        or _rev(point.published_ref, cwd) != expected
        or _rev(point.lineage_ref, cwd) != point.lineage_head
    ):
        return None
    return point
