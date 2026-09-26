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
"""

from __future__ import annotations

from pathlib import Path

from . import activity, obligations, tracking, tracking_write


def apply_claim_add(args: dict) -> dict:
    """Registered as the ``claim_add`` verb. Mirrors the former
    ``claims_cli._claims_add`` transaction (the RecordLock body only -- the
    owner-ref cross-machine resolution/deferral and coordination-readiness
    gate stay in the CLI, resolved before a local ``yaml_path`` is even
    known). Returns ``{"error": "frozen", ...}`` for an owner-frozen
    rejection, never raising."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    kind = args["kind"]
    ref = args["ref"]
    note = args.get("note") or ""

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
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
        tracking.add_resource_claim(record, claim, save=False)
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
    return {"ok": True, "action": action}


def apply_claim_settle(args: dict) -> dict:
    """Registered as the ``claim_settle`` verb."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    ref = args["ref"]
    disposition = args["disposition"]

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        match = next((c for c in record.resources if c.ref == ref), None)
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
    return {"ok": True, "kind": settled.kind, "disposition": disposition}


tracking_write.register_verb("claim_add", apply_claim_add)
tracking_write.register_verb("claim_release", apply_claim_release)
tracking_write.register_verb("claim_settle", apply_claim_settle)
