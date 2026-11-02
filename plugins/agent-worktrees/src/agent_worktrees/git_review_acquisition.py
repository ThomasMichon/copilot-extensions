"""Physical preconditions for the existing Git owner's exact review fetch."""

from pathlib import Path

from .git_repository_environment import repository_identity_env
from .pr_review_source import SourceIncomplete, _OID, _owned_git


def validate_selection(root, head, common, *, checkpoint, deadline):
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise SourceIncomplete("source_repository_not_selected")
    if any(not isinstance(oid, str) or _OID.fullmatch(oid) is None for oid in (head, common)) or len(head) != len(common):
        raise SourceIncomplete("invalid_exact_source_range")
    environment = repository_identity_env()
    environment.update(GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")

    def git(*args):
        return _owned_git(root, checkpoint, deadline, environment, 65536, args).stdout.decode("utf-8").strip()

    common_dir = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    git_dir = Path(git("rev-parse", "--absolute-git-dir")).resolve()
    if (
        common_dir != git_dir or common_dir not in (root, root / ".git")
        or (common_dir / "worktrees").exists()
        or (common_dir / "objects" / "info" / "alternates").exists()
        or (common_dir / "objects" / "info" / "http-alternates").exists()
        or git("rev-parse", "--is-shallow-repository") != "true"
    ):
        raise SourceIncomplete("dedicated_shallow_source_required")
    checkpoint()


def fetch_exact(root, remote, head, common, *, budget, checkpoint):
    """Delegate transport/auth to git_ops; no transport exists in this module."""
    from .git_ops import fetch_review_objects

    return fetch_review_objects(remote, head, common, cwd=root, timeout=budget, checkpoint=checkpoint)
