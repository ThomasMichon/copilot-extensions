"""Persist provider observations only against their unchanged PR authority."""

from __future__ import annotations

from . import claim_history, pr_publication_state, pr_publish, tracking
from .providers.base import PullResult


def persist_observation(
    record: tracking.WorktreeRecord,
    active: tracking.PRRecord,
    state: str,
    resolved: str,
    provider_terminal: bool,
    pull: PullResult,
    *,
    best_effort: bool = False,
) -> None:
    from . import pr_ops

    with tracking._RecordLock(record.yaml_path, blocking=not best_effort) as lk:
        if not lk.acquired:
            return
        fresh = tracking.load_record(record.yaml_path)
        current = pr_publish._publication_pr(fresh, active)
        if current is None or pr_publication_state.authority(current) != pr_publication_state.authority(active):
            return
        claim_ref = pr_ops._pr_claim_ref(active)
        if not any(claim.ref == claim_ref for claim in fresh.resources):
            pending_claim = next((claim for claim in record.resources
                                  if claim.kind == "pr" and claim.ref == claim_ref), None)
            if pending_claim is not None:
                tracking.add_resource_claim(fresh, pending_claim, save=False)
        before = (current.state, current.closed_at, current.head_sha)
        claimed_ref = released_ref = None
        if provider_terminal:
            current.state = "merged" if active.state == "merged" else resolved
            current.closed_at = current.closed_at or tracking._now_iso()
            released_ref = pr_ops._release_pr_claim(fresh, current)
        elif state == "open":
            current.state = "open"
            current.closed_at = ""
            current.head_sha = current.head_sha or pull.head_sha
            claimed_ref = pr_ops._ensure_pr_claim(fresh, current)
            if resolved == "merged":
                current.state = resolved
                current.closed_at = tracking._now_iso()
        if claimed_ref or released_ref or before != (current.state, current.closed_at, current.head_sha):
            tracking.save_record(fresh)
        pr_publication_state.copy_pr(active, current)
        record.resources = fresh.resources
        for name in ("status", "completed_at", "status_note_at", "last_finalized_at"):
            setattr(record, name, getattr(fresh, name))
    if released_ref or claimed_ref:
        claim_history.record_pr_event(
            released_ref or claimed_ref, worktree_id=record.worktree_id,
            machine=record.execution_machine,
            event="released" if released_ref else "claimed",
            note=resolved if released_ref else "", project=record.repo,
        )
