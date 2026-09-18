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


def allocation_lock(tracking_path: Path) -> tracking._RecordLock:
    """Cross-process lock serializing the codename read -> pick -> record-write
    sequence for one project's tracking directory.

    ``existing_codenames``/``assign_new_codename`` only *read* the tracking
    directory; the actual write happens separately (``tracking.create_new_record``
    or ``ensure_codename``'s ``save_record``). Without a shared lock spanning
    both halves, two concurrent creators can scan the same existing set and
    pick the same candidate, after which a later ``find_record_by_codename``
    lookup is ambiguous. Callers hold this ``with`` block across the whole
    scan+pick+persist window -- not just the scan.

    Backed by ``tracking._RecordLock`` (already the tracking store's own
    cross-process RMW primitive) against a sentinel lock path -- ``@codenames``
    is not a real worktree id, so it never collides with one (worktree ids are
    always ``<machine>-<platform>-<timestamp>-<suffix>[-k]``, ``sys-*``, or the
    reserved ``@anchor`` -- none of which is ``@codenames``).
    """
    return tracking._RecordLock(
        tracking_path / "@codenames.lock", require_sidecar=True,
    )


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
    unchanged, no write performed). Holds ``allocation_lock`` across the
    scan+pick+persist sequence so a concurrent backfill/create cannot pick
    the same candidate.
    """
    if record.codename:
        return record
    with allocation_lock(tracking_path):
        # Re-check under the lock: another process may have backfilled (or
        # even re-saved with a different codename) this exact record between
        # our caller's read and this call.
        current = tracking.load_record_by_id(record.worktree_id, tracking_path=tracking_path)
        if current is not None and current.codename:
            record.codename = current.codename
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
