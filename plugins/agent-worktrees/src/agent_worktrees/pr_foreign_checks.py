"""Verify a reachable delegated candidate without borrowing caller Git state."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, cast

from . import pr_publication_checks as checks
from .config import RepoConfig
from .providers.base import PRProvider
from .pr_worker_config import matches_remote


def source_binding(
    repository: str, head: str, source_repository: str | None,
    workspace: str | None, repo: RepoConfig, provider: PRProvider, *, token: str | None = None,
) -> tuple[str, str]:
    """Resolve the actual GitHub fork, never a target-derived repository name."""
    owner, separator, branch = head.partition(":")
    if not separator:
        if source_repository and source_repository.casefold() != repository.casefold():
            raise checks.CheckError("An unqualified head must use the target repository.")
        return repository, head
    selected = source_repository
    if not selected and workspace:
        from .pr_publish import _identity
        urls = checks._git(Path(workspace), "remote", "get-url", "--push", "--all", repo.remote).splitlines()
        identities = {_identity(url, workspace) for url in urls}
        if len(identities) == 1:
            endpoint, _, candidate = identities.pop().partition("/")
            target_identity = _identity("https://" + provider.authority_endpoint(repo.pr.api_base) + "/" + repository, workspace)
            if endpoint.casefold() == target_identity.partition("/")[0]:
                selected = candidate
    if not selected and not workspace:
        reader = getattr(provider, "resolve_head_repository", None)
        if callable(reader):
            selected = reader(repository, owner, api_base=repo.pr.api_base, token=token)
    if not selected:
        raise checks.CheckError("A qualified foreign head needs its actual fork; pass --source-repo owner/repository.")
    parts = selected.split("/")
    if (
        len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        or any(part in (".", "..") for part in parts) or parts[0].casefold() != owner.casefold()
        or not branch or ":" in branch or branch.startswith("refs/")
    ):
        raise checks.CheckError("--source-repo must name the exact fork owned by the qualified head.")
    return selected, branch


def observe(provider: PRProvider, repository: str, branch: str, *,
            api_base: str, token: str | None) -> str:
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
    return cast(str, result["head_sha"])


def target_ref(root: Path, repository: str, branch: str, repo: RepoConfig, head: str) -> dict[str, str]:
    """Resolve an already-fetched target independently of the source push remote."""
    from .worktree_base_preparation import _fetch_rules, _mapped_source

    source = "refs/heads/" + branch
    remotes = checks._git(root, "remote").splitlines()
    for remote in sorted(remotes, key=lambda value: (value != repo.remote, value)):
        urls = checks._git(root, "remote", "get-url", "--all", remote).splitlines()
        if len(urls) != 1 or not matches_remote(urls[0], repository, repo.pr, str(root)):
            continue
        rules = _fetch_rules(root, remote)
        if any(_mapped_source(source, pattern, source) is not None for pattern in rules.exclusions):
            continue
        refs = {
            mapped for left, right in rules.mappings
            if (mapped := _mapped_source(source, left, right)) is not None
        }
        for ref in sorted(refs):
            if not ref.startswith("refs/remotes/"):
                continue
            try:
                if checks._commit(root, ref) == head:
                    return {"remote": remote, "url": urls[0], "ref": ref}
            except checks.CheckError:
                continue
    raise checks.CheckError("source workspace has no current fetched ref for the selected target; refresh before reviewing")


def qualify(
    workspace: str, repository: str, branch: str, expected_head: str, repo: RepoConfig,
    provider: PRProvider, *, token: str | None = None,
    source_repository: str | None = None, base_branch: str | None = None,
) -> dict[str, Any]:
    """Run current target checks and fence them with authoritative ref observations."""
    if (
        not isinstance(expected_head, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", expected_head) is None
    ):
        raise checks.CheckError("--expected-head must be a complete lowercase commit ID")
    root = Path(workspace).resolve()
    if not Path(workspace).is_absolute():
        raise checks.CheckError("--source-workspace must be an absolute exact Git root")
    top = Path(checks._git(root, "rev-parse", "--show-toplevel").removesuffix("\n").removesuffix("\r")).resolve()
    if root != top:
        raise checks.CheckError("source workspace must be the exact Git worktree root")
    if checks._git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip() != branch:
        raise checks.CheckError("source workspace is not on the selected publication branch")
    if checks._commit(root, "HEAD") != expected_head:
        raise checks.CheckError("source workspace no longer contains the expected candidate")
    source_repository = source_repository or repository
    urls = checks._git(root, "remote", "get-url", "--push", "--all", repo.remote).splitlines()
    if len(urls) != 1 or not matches_remote(urls[0], source_repository, repo.pr, str(root)):
        raise checks.CheckError("source push destination does not match the selected target")
    if branch == repo.default_branch or not branch or branch.startswith("-"):
        raise checks.CheckError("foreign creation needs a feature branch, not the target default")
    endpoint = repo.pr.api_base
    target_branch = base_branch or repo.default_branch
    source = observe(provider, source_repository, branch, api_base=endpoint, token=token)
    target = observe(provider, repository, target_branch, api_base=endpoint, token=token)
    if source != expected_head:
        raise checks.CheckError("authoritative source branch differs from the reviewed candidate")
    target_binding = target_ref(root, repository, target_branch, repo, target)
    result = checks.before_publish(
        root, target_binding["ref"], repo.pr.publication_checks, expected_head=expected_head,
    )
    if not result["ok"]:
        raise checks.CheckError(checks.failure_detail(result))
    if (
        observe(provider, source_repository, branch, api_base=endpoint, token=token) != source
        or observe(provider, repository, target_branch, api_base=endpoint, token=token) != target
        or checks._commit(root, "HEAD") != expected_head
        or target_ref(root, repository, target_branch, repo, target) != target_binding
    ):
        raise checks.CheckError("source or target moved while qualifying publication")
    return {
        "ok": True, "adapter": "reachable-workspace", "repository": repository,
        "branch": branch, "head": source, "base": target_branch, "base_head": target,
        "target_binding": target_binding,
        "checks": result,
        "source_repository": source_repository,
        "atomic": False,
    }


def after_create(evidence: dict[str, Any], repository: str, repo: RepoConfig,
                 provider: PRProvider, *, token: str | None = None) -> dict[str, Any]:
    """Report a forge race without discarding the newly created PR identity."""
    try:
        source = observe(
            provider, evidence.get("source_repository", repository), evidence["branch"],
            api_base=repo.pr.api_base, token=token,
        )
        base = observe(
            provider, repository, evidence["base"], api_base=repo.pr.api_base, token=token,
        )
        if source != evidence["head"] or base != evidence["base_head"]:
            return {**evidence, "ok": False, "outcome": "changed-after-create",
                    "detail": "Source or target moved; reconcile this PR before further publication."}
        return {**evidence, "outcome": "verified-after-create"}
    except Exception:
        return {**evidence, "ok": False, "outcome": "unknown-after-create",
                "detail": "PR exists, but candidate readback failed; reconcile this PR, do not recreate it."}
