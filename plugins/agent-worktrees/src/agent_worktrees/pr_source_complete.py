"""Guarded local admission around the existing source reconciliation owner."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import re
import subprocess

from . import config as cfg, git_ops, output, pr_complete, pr_config, pr_publish
from . import pr_source_publication, tracking, worktree_identity
from .git_repository_environment import repository_identity_scope
from .providers.pr_source import same_repository

SCHEMA = "agent-worktrees.pr-source-complete"


def outcome(args, *, source=None, status="not-attempted", result=None, error=""):
    value = {
        "schema": SCHEMA, "version": 1, "repo": args.repo,
        "source": source, "dry_run": bool(args.dry_run),
        "ok": status in ("preview", "confirmed"), "status": status,
    }
    if result is not None:
        value["result"] = result
    if error:
        value["error"] = error
    return value


def _admit(args):
    if (
        not isinstance(args.repo, str) or not args.repo.strip()
        or not isinstance(args.expected_head, str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.expected_head)
        or not isinstance(args.expected_branch, str) or not args.expected_branch
        or args.expected_branch.startswith(("-", "refs/"))
        or any(c in args.expected_branch for c in "\0\r\n")
    ):
        raise ValueError("Guarded completion requires an explicit target, local branch and full lowercase Git object ID")
    if args.worktree_id is not None or args.config is not None:
        raise ValueError("Guarded completion refuses a worktree selector or explicit configuration")
    config = cfg.load_config()
    if config.explicit_pr_target is not None:
        raise ValueError("Guarded completion requires the local source owner's configuration")
    resolution = pr_config.resolve_repo_config_for_slug(config, args.repo)
    if (
        not resolution.resolved or not resolution.same_as_active
        or resolution.repo_name != config.repo_name
        or resolution.repo_config != config.default_repo
        or not config.default_repo.pr.enabled
    ):
        raise ValueError("Completion target must identify this source owner's enabled repository")
    root = Path.cwd().resolve()
    repo = config.default_repo
    remote_url = pr_source_publication._git(root, "remote", "get-url", repo.remote)
    canonical = pr_config._resolve_repo_remote(config, repo)
    if not canonical or not same_repository(
        pr_publish._identity(remote_url, str(root)),
        pr_publish._identity(canonical, str(root)), repo.pr.provider,
    ):
        raise ValueError("Completion upstream remote differs from the source owner's canonical target")
    record = worktree_identity.tracked_invocation_record(
        root, project=config.repo_name, anchor=Path(config.default_repo.anchor),
    )
    if record is None:
        raise ValueError("Guarded completion requires its own existing managed source worktree")
    record = pr_source_publication._caller(record.worktree_id, config)
    if Path(record.worktree_path).resolve() != root:
        raise ValueError("Run guarded completion from the exact recorded source Git top level")
    resolved = tracking.resolve_worktree_path(record.worktree_id, config.default_repo.worktree_root)
    if Path(resolved).resolve() != root:
        raise ValueError("Completion owner resolves a different source worktree")
    branch = pr_source_publication._git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != args.expected_branch:
        raise ValueError("Source branch differs from expected_branch")
    pr_source_publication._git(root, "check-ref-format", "refs/heads/" + branch)
    if branch == config.default_repo.default_branch:
        raise ValueError("Guarded completion cannot reconcile the default branch")
    if pr_source_publication._git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Dirty source worktree blocks completion")
    head = pr_source_publication._git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head != args.expected_head:
        raise ValueError("Source HEAD differs from expected_head")
    source = {"worktree_id": record.worktree_id, "branch": branch, "expected_head": head}
    binding = (source, str(root), config.default_repo, remote_url)
    return config, binding


def complete(args):
    with repository_identity_scope():
        return _complete(args)


@contextmanager
def _source_lock(root: str) -> Iterator[None]:
    git_dir = Path(pr_source_publication._git(root, "rev-parse", "--absolute-git-dir"))
    with (git_dir / "agent-worktrees-complete.lock").open("a+b") as handle:
        pr_publish._lock_publish_file(handle)
        try:
            yield
        finally:
            pr_publish._unlock_publish_file(handle)


def _complete(args):
    source, result, invoked = None, None, False
    try:
        config, binding = _admit(args)
        source, root, _repo, _remote_url = binding
        with _source_lock(root):
            def recheck():
                with pr_publish.publish_lock(root), pr_publish.metadata_lock(
                    source["worktree_id"], project=config.repo_name,
                ):
                    fresh_config, fresh = _admit(args)
                    if fresh_config.repo_name != config.repo_name or fresh != binding:
                        raise ValueError("Source binding changed before reconciliation")

            recheck()
            invoked = True
            result = pr_complete.complete_worktree(
                source["worktree_id"], config, dry_run=args.dry_run,
                validate_source=recheck,
            )
        if result.get("success") is not True:
            return outcome(
                args, source=source, status="unknown", result=result,
                error=result.get("error") or "Source reconciliation is unconfirmed; inspect retained Git state before retrying",
            )
        return outcome(
            args, source=source, status="preview" if args.dry_run else "confirmed", result=result,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, git_ops.GitError) as exc:
        return outcome(
            args, source=source, status="unknown" if invoked else "not-attempted",
            result=result, error=str(exc),
        )


def emit(args, result):
    output._json_output(result) if args.json else print(result)
    return 0 if result["ok"] else 1


def project_unavailable(argv, message):
    import argparse
    from . import pr_state_cli

    parser = argparse.ArgumentParser(allow_abbrev=False)
    pr_state_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return emit(args, outcome(args, error=message))
