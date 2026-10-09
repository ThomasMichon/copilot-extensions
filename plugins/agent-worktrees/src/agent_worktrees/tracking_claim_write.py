"""``claim_add``/``claim_release``/``claim_settle`` verbs.

``agent-worktrees-authoritative-daemon`` effort, Phase 3 -- the third
migrated call-site cluster: the outbound resource-claim ledger's three
single-worktree write transactions (``claims add``/``release``/``settle``,
previously inline in ``claims_cli.py``). A "closely related cluster"
landed together, per the effort README's own Phase 3 guidance, mirroring
``tracking_followup_write.py``'s own shape exactly.

**Deliberately excludes** ``claims sweep``/``claims reconcile-at-rest``
(multi-worktree batch operations iterating every local ledger under one
sweep, not a single-worktree transaction) and the cross-machine
``owner_ref`` resolution/deferral path (a CLI-level concern resolved
BEFORE a local ``yaml_path`` is even known -- there is nothing to migrate
when the answer is "deferred to the lease mirror, no local write"). Both
stay call-site-level, migrated (if ever) in a later, narrower slice.

**No cross-project trace-scoping concern here either** (unlike
``status_disposition_write``'s ``status_reported`` event): none of
``claim_added``/``claim_released``/``claim_settled`` are stage-mapped in
``activity.HANDOFF_STAGE_MAP``, so ``activity.log_event`` never reaches
``handoff_trace.append_event`` for any of them.

``agent-worktrees-authoritative-daemon`` effort, Phase 3's fifth cluster
(this same PR) reuses ``apply_claim_settle`` for ``handoff_cutover.py``'s
``_settle_predecessor_session_claim`` repair via the ``skip_if_released``
guard above, rather than registering a new verb for it -- both wrap the
identical ``tracking.settle_resource_claim`` transaction, only differing
in whether an already-``released`` claim is a silent no-op (repair) or
resettled to the requested disposition (the public CLI command, which
also supports settling explicitly TO ``released``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import activity, claim_history, obligations, tracking, tracking_write
from .execution_spaces import ExecutionSpaceError, require_project_record_mutation


def _authority_rejection(record: tracking.WorktreeRecord) -> dict | None:
    try:
        require_project_record_mutation(record)
    except ExecutionSpaceError as exc:
        logging.getLogger(__name__).warning(
            "Rejecting claim mutation for %s: %s", record.worktree_id, exc,
        )
        return {"error": "rejected", "message": str(exc)}
    return None


def apply_claim_add(args: dict) -> dict:
    """Registered as the ``claim_add`` verb. Mirrors the former
    ``claims_cli._claims_add`` transaction (the RecordLock body only -- the
    owner-ref cross-machine resolution/deferral and coordination-readiness
    gate stay in the CLI, resolved before a local ``yaml_path`` is even
    known). Returns ``{"error": "frozen", ...}`` / ``{"error": "rejected",
    ...}`` for an expected rejection -- never raises ``ValueError`` past
    this function, which (2026-09-27 PR review finding) would otherwise be
    swallowed by ``CoalescingServer`` when dispatched via the daemon and
    surface to the CLI as an `AmbiguousWriteOutcome`, turning a
    deterministic, non-mutating validation failure into an apparently
    unknown write outcome."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    kind = args["kind"]
    ref = args["ref"]
    note = args.get("note") or ""

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        if rejection := _authority_rejection(record):
            return rejection
        if record.status in {"finalizing", "orphaned"}:
            return {
                "error": "frozen",
                "message": (
                    f"claims add: owner worktree {worktree_id} is {record.status}; "
                    "creator ownership is frozen and cannot accept new resources"
                ),
            }
        was_finalized = record.status == "finalized"
        claim = tracking.ResourceClaim(
            kind=kind,
            ref=ref,
            created_at=tracking._now_iso(),
            state=obligations.ACTIVE,
            note=note,
        )
        try:
            tracking.add_resource_claim(record, claim, save=False)
        except ValueError as exc:
            # Covers every other rejection `add_resource_claim` itself
            # enforces (e.g. a completed system/bridge record, or a ref
            # collision with a reservation-held claim) -- caught generically
            # rather than duplicating that predicate here, so this verb can
            # never drift from the one place the invariant is enforced.
            return {"error": "rejected", "message": str(exc)}
        reopened = was_finalized and record.status == "active"
        # worktree-finality-and-obligations Phase 2: on reopen, surface what
        # the earlier finalize's `release_all_resources` cascade let go --
        # reopening restores the worktree to `active`, never those resources.
        released_by_finalize = list(record.last_finalize_released) if reopened else []
        tracking.save_record(record, yaml_path)

    activity.log_event(
        "claim_added",
        worktree_id=worktree_id,
        kind=kind,
        ref=ref,
        state=obligations.ACTIVE,
        reopened=reopened,
    )
    claim_history.record_event(
        kind=kind, ref=ref, worktree_id=worktree_id, machine=record.machine,
        event="claimed", session_id=args.get("session_id"), project=record.repo,
    )
    return {
        "ok": True,
        "state": obligations.ACTIVE,
        "reopened": reopened,
        "released_by_finalize": [
            {"kind": c.kind, "ref": c.ref, "note": c.note} for c in released_by_finalize
        ],
    }


def apply_claim_release(args: dict) -> dict:
    """Registered as the ``claim_release`` verb."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    ref = args["ref"]
    remove = bool(args.get("remove"))

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        if rejection := _authority_rejection(record):
            return rejection
        match = next((c for c in record.resources if c.ref == ref), None)
        if match is None:
            return {"error": "not_found"}
        reservation = tracking.claim_handoff_reservation(record, match)
        if reservation:
            return {
                "error": "reserved",
                "message": (
                    f"claim {ref} is reserved by offered handoff bundle "
                    f"{reservation}; accept, decline, or cancel it first"
                ),
            }
        kind = match.kind
        if remove:
            record.resources = [c for c in record.resources if c.ref != ref]
            action = "removed"
        else:
            match.state = "released"
            action = "released"
        tracking.save_record(record, yaml_path)

    activity.log_event(
        "claim_released", worktree_id=worktree_id, kind=kind, ref=ref, action=action
    )
    claim_history.record_event(
        kind=kind, ref=ref, worktree_id=worktree_id, machine=record.machine,
        event=action, session_id=args.get("session_id"), project=record.repo,
    )
    return {"ok": True, "action": action}


def apply_claim_settle(args: dict) -> dict:
    """Registered as the ``claim_settle`` verb.

    ``skip_if_released`` (default ``False``, preserving the public ``claims
    settle`` CLI command's existing behavior unchanged) is an opt-in guard
    for a best-effort repair caller (``handoff_cutover.py``'s
    ``_settle_predecessor_session_claim``): when set and the matched claim
    is already ``released``, this returns a silent ``{"ok": True, "skipped":
    "released"}`` no-op instead of settling it, so a ``deregister_session``
    that raced ahead and released the claim first is never resurrected back
    to another disposition (mirrors ``finalize.py``'s
    ``_settle_current_session_claim`` guard). Checked BEFORE the
    reservation check below (2026-09-27 PR review finding) so a released
    claim that still carries a stale reservation still silently no-ops --
    the old inline repair never reached a reservation check once a claim
    was already released, and reordering this after it would surface a
    spurious ``{"error": "reserved"}`` instead. Must run inside this same
    locked transaction, not at the caller -- checking then dispatching as
    two separate steps would reopen exactly the race this guards against.

    Always hard-requires the sidecar (2026-09-27 PR review findings): the
    pre-migration inline repair's own OUTER ``_RecordLock`` degraded on
    contention, but its final ``tracking.save_record`` call already
    hard-required the sidecar internally (``save_record`` never accepted a
    softer policy) -- so on real contention the old code's net effect was
    ALWAYS to fail the write and let it fall through to this repair's own
    best-effort ``contextlib.suppress(Exception)``, never to persist a
    stale in-memory snapshot without cross-process exclusion. Matching
    that net effect here (rather than genuinely degrading) avoids silently
    resurrecting an already-``released`` claim if a concurrent
    ``deregister_session`` released it between this verb's own load and
    save.
    """
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    ref = args["ref"]
    disposition = args["disposition"]
    skip_if_released = bool(args.get("skip_if_released"))

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        if rejection := _authority_rejection(record):
            return rejection
        match = next((c for c in record.resources if c.ref == ref), None)
        if skip_if_released and match is not None and match.state == "released":
            return {"ok": True, "skipped": "released"}
        reservation = (
            tracking.claim_handoff_reservation(record, match) if match is not None else ""
        )
        if reservation:
            return {
                "error": "reserved",
                "message": (
                    f"claim {ref} is reserved by offered handoff bundle "
                    f"{reservation}; accept, decline, or cancel it first"
                ),
            }
        settled = tracking.settle_resource_claim(record, ref, disposition, save=False)
        if settled is None:
            return {"error": "not_found"}
        tracking.save_record(record, yaml_path)

    activity.log_event(
        "claim_settled", worktree_id=worktree_id, kind=settled.kind, ref=ref,
        disposition=disposition,
    )
    claim_history.record_event(
        kind=settled.kind, ref=ref, worktree_id=worktree_id, machine=record.machine,
        event="settled", note=disposition, session_id=args.get("session_id"),
        project=record.repo,
    )
    return {"ok": True, "kind": settled.kind, "disposition": disposition}


tracking_write.register_verb("claim_add", apply_claim_add, version=2)
tracking_write.register_verb("claim_release", apply_claim_release, version=2)
tracking_write.register_verb("claim_settle", apply_claim_settle, version=2)
