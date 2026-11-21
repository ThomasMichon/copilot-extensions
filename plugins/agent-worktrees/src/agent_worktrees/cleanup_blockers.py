"""Reporting-only closure-blocker helpers for cleanup/GC skip reasons."""

from __future__ import annotations

from typing import Any

from . import git_ops, prune, tracking
from .closure_descriptor import assemble_closure_descriptor
from .tracking_claims import effective_open_follow_up_count


def _closure_blockers(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    disp: prune.CleanupDisposition,
    *,
    turn_count: int = 0,
) -> list[dict[str, Any]]:
    """The closure descriptor's full ``blockers`` list for ``rec``/``info``/
    ``disp`` (worktree-finality-and-obligations Phase 4) -- REPORTING only,
    never a decision input; cleanup/GC's actual cleanable/not gate stays
    exactly ``disp.cleanable`` (see the Plan bullet's own note on why a full
    switch to the descriptor's ``action_disposition`` was deferred: it
    degrades "safe" to "blocked" whenever evidence isn't a fresh fetch,
    which would newly block cleanups this revalidation path -- no-fetch by
    design -- has always allowed).

    Unlike ``disp.reason`` (only the single FIRST blocking condition
    ``cleanup_disposition`` short-circuited on), ``blockers`` independently
    names every concurrently-true one -- e.g. a worktree that is both
    held-claims AND has open follow-ups reports both, not just whichever
    check ran first. ``evidence_mode``/``repo_fetch_fresh`` are fixed,
    conservative placeholders: this helper only ever reads the returned
    ``blockers`` field, which does not depend on either.
    """
    held_claims = sum(1 for c in rec.resources if c.is_live)
    open_follow_ups = effective_open_follow_up_count(rec)
    descriptor = assemble_closure_descriptor(
        rec, info, disp,
        held_claims=held_claims, open_follow_ups=open_follow_ups,
        evidence_mode="cached", turn_count=turn_count,
        cross_machine_claims=prune.cross_machine_claim_count(rec),
    )
    return descriptor.blockers


def _enrich_reason_with_blockers(reason: str, blockers: list[dict[str, Any]]) -> str:
    """Append every blocker code+count beyond the single one already named in
    ``reason``, when :func:`_closure_blockers` found more than one
    concurrently-true blocker -- a no-op (returns ``reason`` unchanged) when
    there are 0 or 1, since the existing text already says everything."""
    if len(blockers) <= 1:
        return reason
    parts = [f"{b['code']}\u00d7{b['count']}" for b in blockers]
    return f"{reason} \u00b7 blockers: {', '.join(parts)}"
