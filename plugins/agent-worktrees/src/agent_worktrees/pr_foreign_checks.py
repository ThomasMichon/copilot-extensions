"""Verify a reachable delegated candidate without borrowing caller Git state."""
from __future__ import annotations

from pathlib import Path
import re

from . import pr_publication_checks as checks
from .pr_worker_config import matches_remote


def observe(provider, repository: str, branch: str, *, api_base: str, token) -> str:
    reader = getattr(provider, "observe_branch", None)
    if not callable(reader):
        raise checks.CheckError("provider cannot qualify an authoritative source branch")
    result = reader(repository, branch, api_base=api_base, token=token)
    if (
        not isinstance(result, dict)
        or result.get("repository") != repository or result.get("branch") != branch
        or not isinstance(result.get("head_sha"), str)
        or re.fullmatch(r"[0-9a-f]{40,64}", result["head_sha"]) is None
    ):
        raise checks.CheckError("provider returned a mismatched or invalid branch observation")
    return result["head_sha"]


def qualify(
    workspace: str, repository: str, branch: str, expected_head: str, repo, provider, *, token=None,
) -> dict:
    """Run current target checks and fence them with authoritative ref observations."""
    if (
        not isinstance(expected_head, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", expected_head) is None
    ):
        raise checks.CheckError("--expected-head must be a complete lowercase commit ID")
    root = Path(workspace).resolve()
    if not Path(workspace).is_absolute():
        raise checks.CheckError("--source-workspace must be an absolute exact Git root")
    top = Path(checks._git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if root != top:
        raise checks.CheckError("source workspace must be the exact Git worktree root")
    if checks._git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip() != branch:
        raise checks.CheckError("source workspace is not on the selected publication branch")
    if checks._commit(root, "HEAD") != expected_head:
        raise checks.CheckError("source workspace no longer contains the expected candidate")
    urls = checks._git(root, "remote", "get-url", "--push", "--all", repo.remote).splitlines()
    if len(urls) != 1 or not matches_remote(urls[0], repository, repo.pr, str(root)):
        raise checks.CheckError("source push destination does not match the selected target")
    if branch == repo.default_branch or not branch or branch.startswith("-"):
        raise checks.CheckError("foreign creation needs a feature branch, not the target default")
    endpoint = repo.pr.api_base
    source = observe(provider, repository, branch, api_base=endpoint, token=token)
    target = observe(provider, repository, repo.default_branch, api_base=endpoint, token=token)
    if source != expected_head:
        raise checks.CheckError("authoritative source branch differs from the reviewed candidate")
    base = f"{repo.remote}/{repo.default_branch}"
    if checks._commit(root, base) != target:
        raise checks.CheckError("source workspace target ref is stale; refresh before reviewing")
    result = checks.before_publish(
        root, base, repo.pr.publication_checks, expected_head=expected_head,
    )
    if not result["ok"]:
        raise checks.CheckError(checks.failure_detail(result))
    if (
        observe(provider, repository, branch, api_base=endpoint, token=token) != source
        or observe(provider, repository, repo.default_branch, api_base=endpoint, token=token) != target
        or checks._commit(root, "HEAD") != expected_head
    ):
        raise checks.CheckError("source or target moved while qualifying publication")
    return {
        "ok": True, "adapter": "reachable-workspace", "repository": repository,
        "branch": branch, "head": source, "base": repo.default_branch, "base_head": target,
        "checks": result,
        "atomic": False,
    }


def after_create(evidence: dict, repository: str, repo, provider, *, token=None) -> dict:
    """Report a forge race without discarding the newly created PR identity."""
    try:
        source = observe(
            provider, repository, evidence["branch"], api_base=repo.pr.api_base, token=token,
        )
        base = observe(
            provider, repository, repo.default_branch, api_base=repo.pr.api_base, token=token,
        )
        if source != evidence["head"] or base != evidence["base_head"]:
            return {**evidence, "ok": False, "outcome": "changed-after-create",
                    "detail": "Source or target moved; reconcile this PR before further publication."}
        return {**evidence, "outcome": "verified-after-create"}
    except Exception:
        return {**evidence, "ok": False, "outcome": "unknown-after-create",
                "detail": "PR exists, but candidate readback failed; reconcile this PR, do not recreate it."}
