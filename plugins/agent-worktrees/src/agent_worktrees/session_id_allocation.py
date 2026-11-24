"""Codename-based IDs for new session worktrees.

A new session worktree is named ``<codename>-<4hex>`` (for example
``thermal-potato-461b``) instead of ``<machine>-<platform>-<ts>-<4hex>``. The
shorter name keeps checkout paths well below the Windows MAX_PATH limit. The
record's codename is always the ID's codename part. Existing timestamped IDs
keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path
import sys

from . import codename, codename_tracking, config as cfg, git_ops
from .worktree_naming import codename_session_worktree_id, parse_codename_session_worktree_id

_MAX_ATTEMPTS = 50


def _branch_exists(anchor: str, worktree_id: str) -> bool:
    try:
        result = git_ops.git(
            "for-each-ref", "--format=%(refname)",
            f"refs/heads/worktree/{worktree_id}",
            f"refs/remotes/*/worktree/{worktree_id}",
            cwd=anchor, check=False,
        )
    except (OSError, RuntimeError):
        return False
    return result.returncode == 0 and bool((result.stdout or "").strip())


def _in_use(config: cfg.Config, records: Path, worktree_id: str) -> bool:
    repo = config.default_repo
    return (
        (Path(repo.worktree_root) / worktree_id).exists()
        or (records / f"{worktree_id}.yaml").exists()
        or _branch_exists(repo.anchor, worktree_id)
    )


def allocate(config: cfg.Config, suffix: str, *, tracking_path: Path | None = None) -> str:
    """Choose an unused ``<codename>-<suffix>`` ID for a new session worktree.

    A codename already assigned in this project, or an ID whose folder,
    tracking record or ``worktree/<id>`` branch (local or remote-tracking)
    exists, is skipped and another codename is drawn. The caller reads the
    codename again under the allocation lock with :func:`codename_for`.
    """
    records = (
        tracking_path if tracking_path is not None
        else cfg.project_dir(config.repo_name) / "worktrees"
    )
    wordlist = codename_tracking.wordlist_for_repo(config)
    taken = codename_tracking.existing_codenames(records) if records.is_dir() else set()
    for _ in range(_MAX_ATTEMPTS):
        name = codename.assign_codename(taken, wordlist=wordlist)
        worktree_id = codename_session_worktree_id(name, suffix)
        if not _in_use(config, records, worktree_id):
            return worktree_id
        taken.add(name)
    raise RuntimeError(
        f"could not allocate an unused codename-based worktree id after {_MAX_ATTEMPTS} attempts"
    )


def codename_for(
    worktree_id: str, tracking_path: Path, wordlist: codename.Wordlist | None,
) -> str:
    """The codename to record for a new worktree; call under ``allocation_lock``.

    A codename-based ID carries its own codename. In the rare case that a
    concurrent creation took that codename after :func:`allocate`, the ID is
    already durable (a launch receipt may hold it), so record a different
    codename instead of failing; codename lookup stays unambiguous because it
    reads the record's codename. A timestamped ID gets a new codename.
    """
    named = parse_codename_session_worktree_id(worktree_id)
    if named is not None and named[0] not in codename_tracking.existing_codenames(tracking_path):
        return named[0]
    if named is not None:
        print(
            f"warning: codename {named[0]!r} was assigned concurrently; "
            f"worktree {worktree_id} gets another codename",
            file=sys.stderr,
        )
    return codename_tracking.assign_new_codename(tracking_path, wordlist)


def knowledge_codename(
    harness_id: str, tracking_path: Path, wordlist: codename.Wordlist | None,
) -> str:
    """Prefer the harness codename for its ``-k`` sibling when it is free there."""
    named = parse_codename_session_worktree_id(harness_id)
    if named is not None and named[0] not in codename_tracking.existing_codenames(tracking_path):
        return named[0]
    return codename_tracking.assign_new_codename(tracking_path, wordlist)
