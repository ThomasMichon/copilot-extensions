"""Explicit source-workspace admission and fenced, history-preserving publication.

An explicit workspace is operation-scoped adoption, not a managed worktree or a
portable receipt. The managed caller retains custody; no caller commits, PR
policy or credentials are transplanted into the source repository.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

from . import config as cfg, git_ops, hooks, pr_config, pr_publish
from . import pr_publication_checks, pr_worker_config, publication_deadline, repos, tracking
from .git_push_transport import observed_ref, pinned_destination
from .providers.pr_source import same_repository


class SourcePublicationError(pr_publication_checks.CheckError):
    """Source adoption or publication could not be qualified."""


def _git(workspace: str | Path, *args: str) -> str:
    return git_ops.git(
        "--no-replace-objects", *args, cwd=workspace, isolated_repository=True,
        timeout=30, kill_tree=True,
    ).stdout.strip()


def _sha(value: str, name: str, *, absent: bool = False) -> str:
    if absent and value == "":
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise SourcePublicationError(f"{name} must be a full lowercase Git object ID")
    return value


def _canonical_destination(repo, target_repo: str, workspace: str) -> str:
    matches = [
        entry for entry in repos.list_repos()
        if entry.remote and pr_worker_config.matches_remote(entry.remote, target_repo, repo.pr, workspace)
    ]
    identities = {pr_publish._identity(entry.remote, workspace) for entry in matches}
    if len(identities) != 1 or any(entry.repo_class == "reference" for entry in matches):
        raise SourcePublicationError(
            "Source publication needs one registered writable canonical target destination"
        )
    return matches[0].remote


def validate_source_workspace(repo_cfg, target_repo: str, workspace: str | Path,
                              branch: str, expected_head: str) -> dict:
    """Qualify reachable local source; callers must separately run policy checks.

    The returned binding is freshly computed data, never an attestation accepted
    from another process. A1 can use it with its own authoritative provider-head
    observations and the central publication checks.
    """
    _sha(expected_head, "expected_head")
    root = Path(workspace)
    if not root.is_absolute() or not root.is_dir():
        raise SourcePublicationError("Source workspace must be an existing absolute Git root")
    root = root.resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise SourcePublicationError("Source workspace must be the exact Git top level")
    if not repo_cfg.pr.enabled:
        raise SourcePublicationError("The target repository has not enabled PR publication")
    if (not isinstance(branch, str) or not branch or branch.startswith(("-", "refs/"))
            or any(character in branch for character in "\0\r\n")):
        raise SourcePublicationError("Source publication needs an explicit branch name")
    _git(root, "check-ref-format", "refs/heads/" + branch)
    if branch == repo_cfg.default_branch:
        raise SourcePublicationError("Source publication cannot write the target default branch")
    actual_branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if actual_branch != branch:
        raise SourcePublicationError("Source workspace is not on the explicitly selected branch")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise SourcePublicationError("Dirty source workspace blocks publication")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head != expected_head:
        raise SourcePublicationError("Source HEAD differs from the reviewed expected_head")
    remote = repo_cfg.remote
    if not remote or remote.startswith("-") or any(c.isspace() for c in remote):
        raise SourcePublicationError("Target policy needs an explicit remote name")
    canonical = _canonical_destination(repo_cfg, target_repo, str(root))
    fetch, pushes, target = pr_publish._remote_snapshot(remote, cwd=str(root))
    if fetch.returncode or pushes.returncode or not target.head_identity:
        raise SourcePublicationError("Source remote has an unreadable or split push destination")
    canonical_identity = pr_publish._identity(canonical, str(root))
    if not same_repository(target.head_identity, canonical_identity, repo_cfg.pr.provider):
        raise SourcePublicationError("Source push destination differs from the registered target")
    if not pr_worker_config.matches_remote(
        fetch.stdout.strip(), target_repo, repo_cfg.pr, str(root),
    ):
        raise SourcePublicationError("Source remote does not match the target provider binding")
    settings = pr_publish._transport_settings(remote, cwd=str(root))
    return {
        "repo": target_repo, "workspace": str(root),
        "git_dir": _git(root, "rev-parse", "--absolute-git-dir"),
        "branch": branch, "head": head, "remote": remote,
        "head_repo": target.head_repo, "head_identity": target.head_identity,
        "fetch_url": fetch.stdout.rstrip("\r\n"),
        "push_urls": tuple(pushes.stdout.splitlines()), "transport_settings": settings,
    }


def source_evidence(repo_cfg, target_repo: str, workspace: str | Path,
                    branch: str, expected_head: str) -> dict:
    """Produce recheckable local evidence using only the resolved target policy."""
    binding = validate_source_workspace(repo_cfg, target_repo, workspace, branch, expected_head)
    checks = pr_publication_checks.before_publish(
        binding["workspace"], f"{repo_cfg.remote}/{repo_cfg.default_branch}",
        repo_cfg.pr.publication_checks, expected_head=expected_head, head=expected_head,
    )
    if not checks["ok"]:
        raise SourcePublicationError(pr_publication_checks.failure_detail(checks))
    if validate_source_workspace(repo_cfg, target_repo, workspace, branch, expected_head) != binding:
        raise SourcePublicationError("Source binding changed while collecting publication evidence")
    return {**binding, "publication_checks": checks}


def _caller(worktree_id: str, config: cfg.Config):
    if not worktree_id or Path(worktree_id).name != worktree_id:
        raise SourcePublicationError("Source publication requires a managed caller")
    path = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    if not path.is_file():
        raise SourcePublicationError("Source publication requires an existing managed caller record")
    record = tracking.load_record(path)
    expected = Path(record.worktree_path).resolve()
    from .legacy_wsl_record_locality import record_namespace_matches

    if (record.worktree_id != worktree_id or record.repo != config.repo_name
            or not record_namespace_matches(record, config) or not expected.is_dir()
            or record.status in ("finalizing", "finalized", "archived")):
        raise SourcePublicationError("Caller record does not own this active managed workspace")
    if Path(_git(expected, "rev-parse", "--show-toplevel")).resolve() != expected:
        raise SourcePublicationError("Caller workspace is not its recorded Git root")
    anchor_common = _git(config.default_repo.anchor, "rev-parse", "--git-common-dir")
    caller_common = _git(expected, "rev-parse", "--git-common-dir")
    if (Path(config.default_repo.anchor, anchor_common).resolve()
            != Path(expected, caller_common).resolve()):
        raise SourcePublicationError("Caller is unrelated to the configured managed repository")
    return record


def publish_source(worktree_id: str, config: cfg.Config, *, target_repo: str,
                   workspace: str | Path, branch: str, expected_head: str,
                   expected_remote_head: str | None = None) -> dict:
    """Publish only the reviewed source tip, preserving existing remote history.

    No automatic fetch, merge, squash or rebase is performed. When remote history
    is not in the reviewed candidate, its source owner must merge forward and
    review again. An exact lease fences both an existing branch and first push;
    ancestry is required independently, so the lease never authorizes a rewrite.
    """
    result = {"ok": False, "repo": target_repo, "branch": branch, "head": expected_head,
              "publication_state": "not_pushed"}
    try:
        caller = _caller(worktree_id, config)
        resolution = pr_config.resolve_repo_config_for_slug(config, target_repo)
        if not resolution.resolved:
            raise SourcePublicationError(resolution.error or "Target repository binding is unresolved")
        repo = resolution.repo_config
        timeout = publication_deadline.configured(repo.pr)
        from .providers.attribution import validate_effective_head
        validate_effective_head(
            branch, worktree_id=worktree_id,
            machine=(config.machine, caller.machine, caller.execution_machine),
            source_attribution=repo.pr.source_attribution,
        )
        with pr_publish.metadata_lock(worktree_id, project=config.repo_name,
                                      timeout=publication_deadline.lock_wait(timeout)):
            with pr_publish.publish_lock(str(workspace), push_timeout_seconds=timeout):
                evidence = source_evidence(repo, target_repo, workspace, branch, expected_head)
            root, remote = evidence["workspace"], evidence["remote"]
            with pinned_destination(remote, evidence["push_urls"], evidence["transport_settings"],
                                    fetch_url=evidence["fetch_url"]):
                old_head = observed_ref(remote, evidence["push_urls"], f"refs/heads/{branch}", root)
                _sha(old_head, "remote head", absent=True)
                if expected_remote_head is not None and old_head != _sha(
                    expected_remote_head, "expected_remote_head", absent=True,
                ):
                    raise SourcePublicationError("Remote head changed from expected_remote_head")
                if old_head and not git_ops.is_commit_ancestor(old_head, expected_head, cwd=root):
                    raise SourcePublicationError(
                        "Existing remote history is not in the candidate; fetch and merge forward, then review again"
                    )
                default_branch = repo.default_branch
                _git(root, "check-ref-format", f"refs/heads/{default_branch}")
                base_head = observed_ref(remote, evidence["push_urls"],
                                         f"refs/heads/{default_branch}", root)
                local_base = _git(root, "rev-parse", "--verify",
                                  f"refs/remotes/{remote}/{default_branch}^{{commit}}")
                if not base_head or local_base != base_head:
                    raise SourcePublicationError("Target base changed; fetch and review against the current base")
                binding = {key: value for key, value in evidence.items() if key != "publication_checks"}
                if validate_source_workspace(repo, target_repo, root, branch, expected_head) != binding:
                    raise SourcePublicationError("Source publication evidence changed before transport")
                _caller(worktree_id, config)
                result.update(publication_checks=evidence["publication_checks"],
                              expected_remote_head=old_head, head_identity=evidence["head_identity"],
                              publication_state="unknown")
                with hooks.allow_pr_push():
                    pushed = pr_publish.push_checked(
                        None, remote, f"{expected_head}:refs/heads/{branch}", cwd=root,
                        expected_head_repo=evidence["head_repo"],
                        expected_head_identity=evidence["head_identity"],
                        force_with_lease_expect=old_head, strict_lease=True,
                        timeout=timeout, repo=repo,
                    )
                if not pushed:
                    return {**result, "error": pushed.stderr or pushed.stdout or "Source push failed"}
                result.update(publication_state="pushed", warning=pushed.stderr)
                actual = observed_ref(remote, evidence["push_urls"], f"refs/heads/{branch}", root)
                if actual != expected_head:
                    return {**result, "error": "Push completed, but remote head readback differs",
                            "observed_head": actual}
                return {**result, "ok": True, "observed_head": actual}
    except (ValueError, OSError, git_ops.GitError, TimeoutError, subprocess.SubprocessError) as exc:
        return {**result, "error": str(exc)}
