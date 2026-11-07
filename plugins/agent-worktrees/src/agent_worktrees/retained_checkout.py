"""Receiving-ledger and Git proof for checkouts outside today's allocation root."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import config as cfg, git_ops


def _git(directory: Path, *args: str) -> str:
    try:
        return git_ops.git(
            *args, cwd=directory, timeout=10, kill_tree=True,
            isolated_repository=True,
        ).stdout.strip()
    except subprocess.TimeoutExpired as exc:
        raise ValueError("retained checkout Git proof timed out; authority is unavailable") from exc


def require_git_checkout(checkout: Path, anchor: Path, branch: str) -> None:
    if not checkout.is_absolute() or checkout.resolve() != checkout.absolute():
        raise ValueError("retained checkout must be an absolute nonredirected path")
    if not checkout.is_dir() or checkout.is_symlink() or not (checkout / ".git").is_file():
        raise ValueError("retained checkout is not an ordinary linked Git worktree")
    if Path(_git(checkout, "rev-parse", "--show-toplevel")).resolve() != checkout.resolve():
        raise ValueError("retained checkout is not its Git root")
    common = "rev-parse", "--path-format=absolute", "--git-common-dir"
    if Path(_git(checkout, *common)).resolve() != Path(_git(anchor, *common)).resolve():
        raise ValueError("retained checkout belongs to another Git family")
    if _git(checkout, "symbolic-ref", "--short", "HEAD") != branch:
        raise ValueError("retained checkout branch conflicts with its ledger")
    entries = _git(anchor, "worktree", "list", "--porcelain", "-z").split("\0")
    if f"worktree {checkout}" not in entries and not any(
        field.startswith("worktree ")
        and Path(field[9:]).resolve() == checkout.resolve()
        for field in entries
    ):
        raise ValueError("retained checkout has no Git worktree registration")
    administrative = Path(_git(checkout, "rev-parse", "--absolute-git-dir"))
    if administrative.parent.resolve() != Path(_git(anchor, *common)).resolve() / "worktrees":
        raise ValueError("retained checkout is not a linked checkout of the receiving anchor")
    backpointer = Path((administrative / "gitdir").read_text(encoding="utf-8").strip())
    if not backpointer.is_absolute():
        backpointer = administrative / backpointer
    if backpointer.resolve() != (checkout / ".git").resolve():
        raise ValueError("Git worktree registration points at another location")


def matches(record, config) -> bool:
    """A retained record, not an arbitrary supplied path, selects this checkout."""
    from . import execution_spaces, tracking

    try:
        repo = config.repos.get(record.repo)
        if repo is None or config.repo_name != record.repo:
            return False
        native = cfg.detect_platform()
        if record.platform != native or config.platform != native:
            return False
        execution_spaces.require_current_execution_space(config)
        context = execution_spaces.current_execution_context(config)
        if record.machine_identity is not None and (
            context is None
            or record.machine_identity.machine != context.machine
            or record.machine_identity.environment != context.environment
            or record.machine_identity.execution_machine != context.execution_machine
        ):
            return False
        checkout = Path(record.worktree_path)
        ledger = cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
        if (
            checkout.name != record.worktree_id or record.worktree_id == "@anchor"
            or ledger.is_symlink() or not ledger.is_file()
            or record.yaml_path.resolve() != ledger.resolve()
            or ledger.resolve().parent != ledger.parent.resolve()
        ):
            return False
        current = tracking.load_record(ledger)
        fields = (
            "repo", "worktree_id", "worktree_path", "branch", "creation_nonce",
            "machine", "platform", "machine_identity", "checkout_managed",
        )
        if any(getattr(current, field) != getattr(record, field) for field in fields):
            return False
        if not execution_spaces.require_owner_identity(record.execution_machine, config, canonical_ref=False):
            from .creation_repair_identity import _observed_original_owner

            if not _observed_original_owner(record, config):
                return False
        require_git_checkout(checkout, Path(repo.anchor), record.branch)
        return True
    except (OSError, ValueError, RuntimeError, git_ops.GitError, execution_spaces.ExecutionSpaceError):
        return False
