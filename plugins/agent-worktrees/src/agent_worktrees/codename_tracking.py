"""Per-worktree codename assignment, backfill, and lookup (effort:
``pr-attribution-codenames`` Phase 2, issue #2838).

Phase 1 (:mod:`agent_worktrees.codename`) built the neutral generator and the
declarative wordlist loader; this module wires that generator to the actual
local tracking store: assigning one codename per worktree at ``create`` time,
lazily backfilling a pre-existing record that predates this feature, and
resolving a codename back to its worktree id for ``list``/``resolve``.

Kept as its own module rather than growing ``tracking.py`` or ``__main__.py``
further -- both are at or near their ``tools/check-module-size.py`` ceiling
(see ``tools/module-size-baseline.json``).
"""

from __future__ import annotations

from pathlib import Path

from . import tracking
from .codename import DEFAULT_WORDLIST, Wordlist, assign_codename, load_wordlist_or_default


def wordlist_for_repo(config) -> Wordlist:
    """Resolve the :class:`Wordlist` a repo's config declares, falling back
    to the built-in neutral vocabulary. ``config`` is a loaded
    ``agent_worktrees.config.Config``.
    """
    repo = config.default_repo
    return load_wordlist_or_default(getattr(repo.codename, "wordlist_path", ""))


def existing_codenames(tracking_path: Path) -> set[str]:
    """The set of codenames already assigned within one project's tracking
    directory (local collision check only -- see :mod:`agent_worktrees.codename`
    for the cross-machine-reservation caveat Phase 3 addresses).
    """
    return {
        rec.codename
        for rec in tracking.list_records(tracking_path)
        if rec.codename
    }


def assign_new_codename(tracking_path: Path, wordlist: Wordlist | None = None) -> str:
    """Assign a codename unused within ``tracking_path``'s project."""
    return assign_codename(
        existing_codenames(tracking_path),
        wordlist=wordlist if wordlist is not None else DEFAULT_WORDLIST,
    )


def ensure_codename(
    record: tracking.WorktreeRecord,
    tracking_path: Path,
    wordlist: Wordlist | None = None,
) -> tracking.WorktreeRecord:
    """Lazily backfill ``record.codename`` if absent, persisting the result.

    A worktree record created before this feature (or before a repo opted
    into codename attribution) has no codename. This is the first-touch
    allocation path: idempotent (a record that already has one is returned
    unchanged, no write performed).
    """
    if record.codename:
        return record
    record.codename = assign_new_codename(tracking_path, wordlist)
    tracking.save_record(record)
    return record


def find_record_by_codename(
    tracking_path: Path, codename: str,
) -> tracking.WorktreeRecord | None:
    """Resolve a codename to its worktree record within one project's
    tracking directory, or ``None`` if no record carries that codename.
    """
    if not codename:
        return None
    for rec in tracking.list_records(tracking_path):
        if rec.codename == codename:
            return rec
    return None
