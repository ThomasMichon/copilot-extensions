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


class CodenameAttributionPolicyError(Exception):
    """Raised when a PR-active repo with a custom ``codename.wordlist_path``
    attempts to allocate a NEW codename under an unconfigured/implicit
    ``pr.source_attribution`` (effort codename-attribution-by-default).

    A custom wordlist is a separate, independently-configured risk surface
    from ``source_attribution`` -- it could contain identifying/private
    terms, so a repo that has one configured must explicitly set
    ``source_attribution`` (to ``codename``, ``true``, or ``false``) before
    the new implicit ``"codename"`` default is allowed to draw a NEW
    codename from that vocabulary. This is an ALLOCATION-time gate only: it
    never affects publishing an already-assigned codename, which is decided
    exclusively by that record's own persisted ``codename_source`` (see
    ``providers.attribution``'s publish-time gating helper).
    """


def classify_codename_source(codename_cfg) -> str:
    """Classify a repo's codename provenance ("built-in"/"custom") from its
    PARSED :class:`~agent_worktrees.codename_config.CodenameConfig`, using
    ``wordlist_path_configured`` -- never ``wordlist_path``'s own
    truthiness, and never the resolved :class:`Wordlist` (round-11/13/14
    findings: both are indistinguishable from "no custom wordlist" for a
    malformed value or an unloadable file, respectively). ``codename_cfg``
    may be ``None`` (a caller with no codename config at all) -- treated the
    same as "no custom wordlist configured".
    """
    configured = bool(
        getattr(codename_cfg, "wordlist_path_configured", False)
    ) if codename_cfg is not None else False
    return "custom" if configured else "built-in"


def check_allocation_policy(
    *, pr_enabled: bool, codename_source: str, source_attribution_configured: bool,
) -> None:
    """Raise :class:`CodenameAttributionPolicyError` when a PR-active repo
    with a custom wordlist (``codename_source == "custom"``) has not
    explicitly configured ``pr.source_attribution``. Scoped to PR-active
    repos only (round-22 finding) -- a repo with ``pr.enabled`` false/unset
    can never publish a marker, so gating its allocation is pure friction
    with no safety benefit; the persisted ``codename_source`` already makes
    publish-time resolution fail-closed regardless, whenever PR mode is
    later enabled.
    """
    if pr_enabled and codename_source == "custom" and not source_attribution_configured:
        raise CodenameAttributionPolicyError(
            "This repo has a custom codename.wordlist_path configured and "
            "PR mode enabled, but pr.source_attribution has not been set "
            "explicitly. Set pr.source_attribution to \"codename\", "
            "\"true\", or \"false\" before a new codename may be allocated "
            "-- the implicit \"codename\" default never draws from a "
            "custom, unreviewed vocabulary."
        )


def allocation_lock(tracking_path: Path) -> tracking._RecordLock:
    """Cross-process lock serializing the codename read -> pick -> record-write
    sequence for one project's tracking directory.

    ``existing_codenames``/``assign_new_codename`` only *read* the tracking
    directory; the actual write happens separately (``tracking.create_new_record``
    or ``ensure_codename``'s ``save_record``). Without a shared lock spanning
    both halves, two concurrent creators can scan the same existing set and
    pick the same candidate, after which a later ``find_record_by_codename``
    lookup is ambiguous. Callers hold this ``with`` block across the whole
    scan+pick+persist window -- not just the scan. The `create` paths
    additionally hold it across the actual ``git_ops.create_worktree`` call
    (needed to prevent an allocation failure from ever leaving an orphaned
    checkout -- see the Phase 2 create-path history), so this needs a much
    longer timeout than a plain in-memory RMW: a real ``git worktree add``
    can legitimately take longer than a couple of seconds (network fetch,
    a large repo, a slow disk).

    Backed by ``tracking._RecordLock`` (already the tracking store's own
    cross-process RMW primitive) against a sentinel lock path -- ``@codenames``
    is not a real worktree id, so it never collides with one (worktree ids are
    always ``<machine>-<platform>-<timestamp>-<suffix>[-k]``, ``sys-*``, or the
    reserved ``@anchor`` -- none of which is ``@codenames``). ``require_sidecar``
    means a real failure to acquire the lock in time raises ``TimeoutError``
    (visible to the caller) rather than silently proceeding without
    exclusivity, which would defeat the whole point of this lock.
    """
    #: Generous relative to _RecordLock's 2s default -- covers a concurrent
    #: `create`'s git worktree/branch creation, not just an in-memory RMW.
    _ALLOCATION_LOCK_TIMEOUT = 30.0
    return tracking._RecordLock(
        tracking_path / "@codenames.lock",
        timeout=_ALLOCATION_LOCK_TIMEOUT,
        require_sidecar=True,
    )


def wordlist_for_repo(config) -> Wordlist:
    """Resolve the :class:`Wordlist` a repo's config declares, falling back
    to the built-in neutral vocabulary. ``config`` is a loaded
    ``agent_worktrees.config.Config``.

    Defensively degrades to the built-in wordlist for any config object
    missing the expected shape (e.g. a minimal stand-in used by a caller
    that doesn't otherwise need a real ``Config``) rather than raising --
    the same fail-soft posture as :func:`load_wordlist_or_default` itself.
    """
    repo = getattr(config, "default_repo", None)
    codename_cfg = getattr(repo, "codename", None)
    path = getattr(codename_cfg, "wordlist_path", "") if codename_cfg is not None else ""
    return load_wordlist_or_default(path)


def allocation_policy_kwargs_for_repo(config) -> dict:
    """Resolve the ``ensure_codename`` allocation-policy kwargs
    (``codename_source``/``pr_enabled``/``source_attribution_configured``)
    from a repo's config, defensively degrading to the safe "no PR mode,
    built-in wordlist" defaults for any config object missing the expected
    shape (e.g. a minimal test stand-in) -- mirrors :func:`wordlist_for_repo`'s
    own fail-soft posture, so a caller with an incomplete config never
    raises here just to determine what to pass through.
    """
    repo = getattr(config, "default_repo", None)
    codename_cfg = getattr(repo, "codename", None)
    prcfg = getattr(repo, "pr", None)
    return {
        "codename_source": classify_codename_source(codename_cfg),
        "pr_enabled": bool(getattr(prcfg, "enabled", False)),
        "source_attribution_configured": bool(
            getattr(prcfg, "source_attribution_configured", False)
        ),
    }


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
    *,
    codename_source: str = "built-in",
    pr_enabled: bool = False,
    source_attribution_configured: bool = False,
) -> tracking.WorktreeRecord:
    """Lazily backfill ``record.codename`` if absent, persisting the result.

    A worktree record created before this feature (or before a repo opted
    into codename attribution) has no codename. This is the first-touch
    allocation path: idempotent (a record that already has one is returned
    unchanged, no write performed). Holds ``allocation_lock`` across the
    scan+pick+persist sequence so a concurrent backfill/create cannot pick
    the same candidate.

    Lock order is always ``allocation_lock`` (outer) then the per-record
    lock (inner) -- never the reverse -- to match every other codename call
    site (the ``create`` paths hold the same order). Never acquire a
    per-record lock and then this module's ``allocation_lock`` from the same
    call stack; that reversed order is a real cross-process deadlock hazard
    against a concurrent path holding them in this module's order.

    ``codename_source``/``pr_enabled``/``source_attribution_configured``
    (codename-attribution-by-default, round-26 finding) centralize the
    allocation-time policy gate HERE, on the actual-new-allocation branch
    only -- never on the "a concurrent writer already assigned one, just
    copy it" branch below, which must never re-validate policy for an
    already-decided codename. This protects every caller
    (``resume``/``status --write``'s backfill, ``create-pr``'s backfill,
    and the ``create``/paired-knowledge preflights, which call this as
    their actual allocation step after their own early, side-effect-safe
    preflight) without each one duplicating the check.
    """
    if record.codename:
        return record
    yaml_path = tracking_path / f"{record.worktree_id}.yaml"
    with allocation_lock(tracking_path):
        # Hold the per-record lock across the existence-check + re-read +
        # save as ONE atomic unit (not just re-reading unlocked): otherwise
        # `retire_record` could still remove the file between an unlocked
        # read and `save_record`'s own (separate) lock acquisition, and that
        # save would recreate the just-deleted tracking file. `save_record`
        # re-locks the same path internally, but `_RecordLock` detects same-
        # thread same-path reentry and safely no-ops the re-acquisition, so
        # nesting here is safe.
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            if not yaml_path.exists():
                # Reaped (by `retire_record`) between the caller's original
                # read and this lock -- never resurrect a deleted worktree's
                # tracking file. Return the caller's copy as-is.
                return record
            current = tracking.load_record(yaml_path)
            if current.codename:
                # A concurrent writer already assigned one -- copy its
                # codename AND provenance (round-12 finding: copying only
                # `.codename` silently dropped the provenance the winning
                # writer already recorded). Never re-validate allocation
                # policy for an already-decided codename.
                record.codename = current.codename
                record.codename_source = current.codename_source
                return record
            check_allocation_policy(
                pr_enabled=pr_enabled,
                codename_source=codename_source,
                source_attribution_configured=source_attribution_configured,
            )
            current.codename = assign_new_codename(tracking_path, wordlist)
            current.codename_source = codename_source
            tracking.save_record(current, yaml_path)
            record.codename = current.codename
            record.codename_source = current.codename_source
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
