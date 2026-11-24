"""Codename-based IDs for new session worktrees.

A new session worktree is named ``<codename>-<4hex>`` (for example
``thermal-potato-461b``) instead of ``<machine>-<platform>-<ts>-<4hex>``. The
shorter name keeps checkout paths well below the Windows MAX_PATH limit. The
record's codename is always the ID's codename part. Existing timestamped IDs
keep working unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

from . import codename, codename_tracking, config as cfg, git_ops
from .worktree_naming import codename_session_worktree_id, parse_codename_session_worktree_id

_MAX_ATTEMPTS = 50
_RESERVATION_TTL_SECONDS = 30 * 24 * 3600


def _branch_exists(anchor: str, worktree_id: str) -> bool:
    """Whether a local or remote-tracking ``worktree/<id>`` branch exists.

    An anchor that does not exist has no branches. Otherwise a failed ref
    inspection raises, so allocation never hands out an ID it could not check.
    """
    if not Path(anchor).exists():
        return False
    try:
        result = git_ops.git(
            "for-each-ref", "--format=%(refname)",
            f"refs/heads/worktree/{worktree_id}",
            f"refs/remotes/*/worktree/{worktree_id}",
            cwd=anchor, check=False,
        )
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"cannot inspect worktree branches in {anchor}: {exc}") from exc
    return result.returncode == 0 and bool((result.stdout or "").strip())


def _in_use(config: cfg.Config, records: Path, worktree_id: str) -> bool:
    repo = getattr(config, "default_repo", None)
    root = getattr(repo, "worktree_root", None)
    anchor = getattr(repo, "anchor", None)
    return (
        (root is not None and (Path(root) / worktree_id).exists())
        or (records / f"{worktree_id}.yaml").exists()
        or (anchor is not None and _branch_exists(anchor, worktree_id))
    )


def _reserve(records: Path, worktree_id: str) -> bool:
    """Atomically reserve ``worktree_id`` in the project of ``records``.

    Each allocator reserves before it returns an ID, so two pending requests
    never receive the same ID even before either one writes its record.
    :func:`_prune` removes a reservation once its record exists (the record
    then blocks reuse) or after a long timeout for an abandoned request.
    """
    directory = records.parent / "reserved-worktree-ids"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(directory / worktree_id, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def _release(records: Path, worktree_id: str) -> None:
    (records.parent / "reserved-worktree-ids" / worktree_id).unlink(missing_ok=True)


def _prune(records: Path, *, now: float | None = None) -> None:
    """Keep only reservations of pending requests, so the store stays bounded."""
    directory = records.parent / "reserved-worktree-ids"
    if not directory.is_dir():
        return
    cutoff = (time.time() if now is None else now) - _RESERVATION_TTL_SECONDS
    for entry in directory.iterdir():
        try:
            if (records / f"{entry.name}.yaml").exists() or entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue


def _knowledge_namespace(config: cfg.Config) -> tuple[cfg.Config, Path] | None:
    """The bound knowledge project that receives this project's ``-k`` siblings."""
    if os.environ.get("AGENT_WORKTREES_NO_PAIR"):
        return None
    repo = getattr(config, "default_repo", None)
    if not (getattr(repo, "requires_external_state_root", False) or getattr(repo, "stateless", False)):
        return None
    name = (getattr(config, "knowledge_repo", "") or "").strip()
    if not name or name == config.repo_name:
        return None
    try:
        return cfg.load_config(project=name), cfg.project_dir(name) / "worktrees"
    except Exception:
        return None


def allocate(
    config: cfg.Config, suffix: str, *, tracking_path: Path | None = None,
    knowledge: tuple[cfg.Config, Path] | None = None,
) -> str:
    """Choose and reserve an unused ``<codename>-<suffix>`` ID for a new session worktree.

    A codename already assigned in this project, an ID that another request
    reserved, or an ID whose folder, tracking record or ``worktree/<id>``
    branch (local or remote-tracking) exists, is skipped and another codename
    is drawn. When a knowledge project is bound, the ``<id>-k`` sibling is
    checked and reserved in that project too: several harness projects can
    share one knowledge project. The caller reads the codename again under
    the allocation lock with :func:`codename_for`.
    """
    records = (
        tracking_path if tracking_path is not None
        else cfg.project_dir(config.repo_name) / "worktrees"
    )
    paired = knowledge if knowledge is not None else _knowledge_namespace(config)
    _prune(records)
    if paired is not None:
        _prune(paired[1])
    wordlist = codename_tracking.wordlist_for_repo(config)
    taken = codename_tracking.existing_codenames(records) if records.is_dir() else set()
    for _ in range(_MAX_ATTEMPTS):
        name = codename.assign_codename(taken, wordlist=wordlist)
        taken.add(name)
        worktree_id = codename_session_worktree_id(name, suffix)
        sibling = f"{worktree_id}-k"
        if _in_use(config, records, worktree_id) or (
            paired is not None and _in_use(paired[0], paired[1], sibling)
        ):
            continue
        if not _reserve(records, worktree_id):
            continue
        if paired is not None and not _reserve(paired[1], sibling):
            _release(records, worktree_id)
            continue
        return worktree_id
    raise RuntimeError(
        f"could not allocate an unused codename-based worktree id after {_MAX_ATTEMPTS} attempts"
    )


def _in_vocabulary(name: str, wordlist: codename.Wordlist | None) -> bool:
    """Whether ``name`` can come from ``wordlist`` (the built-in one when None).

    A codename copied from an ID is recorded with the current vocabulary's
    provenance, so it is reused only when that vocabulary can produce it.
    """
    words = wordlist if wordlist is not None else codename.DEFAULT_WORDLIST
    parts = name.split("-")
    if len(parts) == 1:
        return name in words.nouns
    if len(parts) != 2:
        return False
    if words.pairs:
        return (parts[0], parts[1]) in words.pairs
    return parts[0] in words.adjectives and parts[1] in words.nouns


def _reusable(name: str, tracking_path: Path, wordlist: codename.Wordlist | None) -> bool:
    return (
        _in_vocabulary(name, wordlist)
        and name not in codename_tracking.existing_codenames(tracking_path)
    )


def codename_for(
    worktree_id: str, tracking_path: Path, wordlist: codename.Wordlist | None,
) -> str:
    """The codename to record for a new worktree; call under ``allocation_lock``.

    A codename-based ID carries its own codename. In the rare case that a
    concurrent creation took that codename after :func:`allocate`, the ID is
    already durable (a launch receipt may hold it), so record a different
    codename instead of failing; codename lookup stays unambiguous because it
    reads the record's codename. The ID's codename is also not reused when the
    current vocabulary cannot produce it (for example, the custom wordlist was
    removed after admission), so the recorded provenance stays correct. A
    timestamped ID gets a new codename.
    """
    named = parse_codename_session_worktree_id(worktree_id)
    if named is not None and _reusable(named[0], tracking_path, wordlist):
        return named[0]
    if named is not None:
        print(
            f"warning: codename {named[0]!r} is taken or outside the current vocabulary; "
            f"worktree {worktree_id} gets another codename",
            file=sys.stderr,
        )
    return codename_tracking.assign_new_codename(tracking_path, wordlist)


def knowledge_codename(
    harness_id: str, tracking_path: Path, wordlist: codename.Wordlist | None,
) -> str:
    """Prefer the harness codename for its ``-k`` sibling when it is free there.

    The harness codename is reused only when the knowledge project's own
    vocabulary can produce it, so the sibling's recorded provenance is correct.
    """
    named = parse_codename_session_worktree_id(harness_id)
    if named is not None and _reusable(named[0], tracking_path, wordlist):
        return named[0]
    return codename_tracking.assign_new_codename(tracking_path, wordlist)
