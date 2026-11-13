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


def _gitdir_marker(checkout: Path) -> Path:
    marker = checkout / ".git"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("local checkout has no ordinary linked-worktree marker")
    text = marker.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir: ") or "\n" in text:
        raise ValueError("invalid linked-worktree marker")
    return (checkout / text.removeprefix("gitdir: ")).resolve()


def _git_directory(anchor: Path) -> Path:
    if not anchor.is_absolute():
        raise ValueError("receiving anchor must be absolute")
    marker = anchor / ".git"
    if marker.is_file():
        common = _gitdir_marker(anchor)
        shared = common / "commondir"
        if shared.is_file():
            administrative = common
            relative = shared.read_text(encoding="utf-8").strip()
            if not relative:
                raise ValueError("receiving anchor has an empty common directory")
            common = (common / relative).resolve()
            backpointer = (administrative / "gitdir").read_text(encoding="utf-8").strip()
            if (
                administrative.parent != common / "worktrees" or not backpointer
                or (administrative / backpointer).resolve() != marker.resolve()
            ):
                raise ValueError("receiving linked anchor registration points at another location")
    else:
        common = marker if marker.is_dir() else anchor
    common = common.resolve()
    if not (common / "HEAD").is_file() or not (common / "objects").is_dir():
        raise ValueError("receiving anchor is not a Git repository")
    return common


def _require_linked_checkout(checkout: Path, anchor: Path) -> str:
    """Read Git's own reciprocal membership without starting Git on entry."""
    if not checkout.is_absolute() or checkout.resolve() != checkout.absolute():
        raise ValueError("local checkout must be an absolute nonredirected path")
    marker = checkout / ".git"
    administrative = _gitdir_marker(checkout)
    common = _git_directory(anchor)
    if administrative.parent != common / "worktrees":
        raise ValueError("local checkout belongs to another Git family")
    shared = (administrative / "commondir").read_text(encoding="utf-8").strip()
    if not shared or (administrative / shared).resolve() != common:
        raise ValueError("linked-worktree common directory conflicts with its receiving anchor")
    backpointer = (administrative / "gitdir").read_text(encoding="utf-8").strip()
    if not backpointer or (administrative / backpointer).resolve() != marker.resolve():
        raise ValueError("Git worktree registration points at another location")
    head = (administrative / "HEAD").read_text(encoding="utf-8").strip()
    if not head:
        raise ValueError("linked-worktree registration has no HEAD")
    return head


def _local_head(record, config) -> str | None:
    """Prove a retained local checkout without consulting machine topology."""
    from . import record_cache, tracking

    try:
        repo = config.repos.get(record.repo)
        if repo is None or config.repo_name != record.repo or config.platform != cfg.detect_platform():
            return None
        checkout = Path(record.worktree_path)
        ledger = cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
        if (
            not checkout.is_absolute()
            or Path(record.worktree_id).name != record.worktree_id
            or "/" in record.worktree_id or "\\" in record.worktree_id
            or ledger.is_symlink() or not ledger.is_file()
            or record.yaml_path is None
            or record.yaml_path.resolve() != ledger.resolve()
            or ledger.resolve().parent != ledger.parent.resolve()
        ):
            return None
        if record.worktree_id != "@anchor" and not (checkout / ".git").is_file():
            return None
        current = record_cache.cached_load(ledger, tracking._load_record_uncached)
        if (
            record.receiving_allocation_present and not current.receiving_allocation_present
            and record.receiving_allocation != current.receiving_allocation
        ):
            from .receiving_allocation import recover_missing, require_record

            if not recover_missing(current, ledger.parent, upgrade_journal=False):
                return None
            require_record(current, config, local_record=True)
        fields = (
            "repo", "worktree_id", "worktree_path", "branch", "creation_nonce",
            "checkout_managed",
            "owner_ref", "pair_ref", "receiving_allocation", "receiving_allocation_present",
        )
        if any(getattr(current, field) != getattr(record, field) for field in fields):
            return None
        if record.worktree_id == "@anchor":
            if checkout != Path(repo.anchor) or checkout.resolve() != checkout.absolute():
                return None
            return (_git_directory(checkout) / "HEAD").read_text(encoding="utf-8").strip() or None
        return _require_linked_checkout(checkout, Path(repo.anchor))
    except (AttributeError, KeyError, TypeError, OSError, ValueError, RuntimeError, cfg.yaml.YAMLError, git_ops.GitError):
        return None


def is_local(record, config) -> bool:
    """Prove receiving-store membership, independent of the current branch."""
    return _local_head(record, config) is not None


def recorded_branch_matches(record, config) -> bool | None:
    """Separate branch-sensitive custody from ordinary local entry."""
    if record.worktree_id == "@anchor":
        return None
    head = _local_head(record, config)
    if head is None:
        return None
    return bool(record.branch) and head == f"ref: refs/heads/{record.branch}"


def matches(record, config) -> bool:
    """Prove an original managed checkout outside today's allocation root."""
    if Path(record.worktree_path).name != record.worktree_id or not is_local(record, config):
        return False
    try:
        require_git_checkout(Path(record.worktree_path), Path(config.default_repo.anchor), record.branch)
        return True
    except (OSError, ValueError, RuntimeError, git_ops.GitError):
        return False


def resolve_owner(ref, config, *, child=None) -> Path | None:
    """Resolve an original qualified owner through its actual local ledger."""
    from . import execution_spaces, tracking

    if ref is None or not ref.is_qualified:
        return None
    if (
        not cfg._PROJECT_NAME_RE.fullmatch(ref.project)
        or Path(ref.worktree_id).name != ref.worktree_id
        or "/" in ref.worktree_id or "\\" in ref.worktree_id
    ):
        return None
    try:
        path = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
        if not path.is_file() or path.is_symlink():
            return None
        record = tracking.load_record(path)
        if ref.machine != record.execution_machine:
            from .tracking_lookup import local_pair_subjects

            if (
                child is None or child.owner_ref != child.pair_ref
                or child.pair_claim_ref != ref or not local_pair_subjects(child, record)
            ):
                return None
        receiving = config if config.repo_name == ref.project else execution_spaces.project_record_config(record)
        if is_local(record, receiving):
            return path
    except (AttributeError, KeyError, TypeError, OSError, ValueError, RuntimeError, cfg.yaml.YAMLError, git_ops.GitError):
        pass
    return None
