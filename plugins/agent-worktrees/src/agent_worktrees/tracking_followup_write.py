"""``follow_up_add``/``follow_up_resolve``/``follow_up_dismiss`` verbs.

``agent-worktrees-authoritative-daemon`` effort, Phase 3 -- the second
migrated call-site cluster: the itemized follow-up ledger's three write
transactions (``follow-ups add``/``resolve``/``dismiss``, previously inline
in ``follow_ups_cli.py``). A "closely related cluster" landed together, per
the effort README's own Phase 3 guidance, since all three share the same
``tracking._RecordLock`` -> mutate -> ``save_record`` -> ``activity.
log_event`` shape.

**No cross-project trace-scoping concern here** (unlike
``status_disposition_write``'s ``status_reported`` event): none of
``follow_up_added``/``follow_up_resolved``/``follow_up_dismissed`` are
stage-mapped in ``activity.HANDOFF_STAGE_MAP``, so ``activity.log_event``
never reaches ``handoff_trace.append_event`` (the ambient-``cfg.
active_project()`` sink that migration had to explicitly re-scope) for any
of them -- there is nothing here that a resident daemon's own ambient
project could silently mis-scope.
"""

from __future__ import annotations

from pathlib import Path

from . import activity, tracking, tracking_write


def _follow_up_to_dict(item: tracking.FollowUpRecord) -> dict:
    return {
        "id": item.id,
        "summary": item.summary,
        "state": item.state,
        "revision": item.revision,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "refs": [{"kind": r.kind, "ref": r.ref} for r in item.refs],
        **({"result_ref": item.result_ref} if item.result_ref else {}),
        **({"reason": item.reason} if item.reason else {}),
    }


def apply_follow_up_add(args: dict) -> dict:
    """Registered as the ``follow_up_add`` verb. Mirrors the former
    ``follow_ups_cli._follow_ups_add`` transaction exactly: raises no
    exception of its own -- an owner-frozen rejection (``ValueError`` from
    ``tracking.add_follow_up``) is caught and returned as a JSON-safe
    ``{"error": ...}`` result for the call site to render."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    summary = args["summary"]
    refs = [
        tracking.FollowUpRef(kind=r["kind"], ref=r["ref"]) for r in args.get("refs") or []
    ]

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        was_finalized = record.status == "finalized"
        try:
            item = tracking.add_follow_up(record, summary, refs=refs, save=False)
        except ValueError as exc:
            return {"error": "frozen", "message": str(exc)}
        reopened = was_finalized and record.status == "active"
        tracking.save_record(record, yaml_path)

    activity.log_event(
        "follow_up_added",
        worktree_id=worktree_id,
        follow_up_id=item.id,
        summary=item.summary,
        refs=[f"{r.kind}:{r.ref}" for r in item.refs],
        reopened=reopened,
    )
    return {"ok": True, "follow_up": _follow_up_to_dict(item), "reopened": reopened}


def apply_follow_up_resolve(args: dict) -> dict:
    """Registered as the ``follow_up_resolve`` verb."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    follow_up_id = args["follow_up_id"]
    result_ref = args.get("result_ref")

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        item = tracking.resolve_follow_up(
            record, follow_up_id, result_ref=result_ref, save=False
        )
        if item is None:
            return {"error": "not_found"}
        tracking.save_record(record, yaml_path)

    activity.log_event(
        "follow_up_resolved",
        worktree_id=worktree_id,
        follow_up_id=item.id,
        result_ref=item.result_ref,
    )
    return {"ok": True, "follow_up": _follow_up_to_dict(item)}


def apply_follow_up_dismiss(args: dict) -> dict:
    """Registered as the ``follow_up_dismiss`` verb."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    follow_up_id = args["follow_up_id"]
    reason = args["reason"]

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        item = tracking.dismiss_follow_up(record, follow_up_id, reason=reason, save=False)
        if item is None:
            return {"error": "not_found"}
        tracking.save_record(record, yaml_path)

    activity.log_event(
        "follow_up_dismissed",
        worktree_id=worktree_id,
        follow_up_id=item.id,
        reason=reason,
    )
    return {"ok": True, "follow_up": _follow_up_to_dict(item)}


tracking_write.register_verb("follow_up_add", apply_follow_up_add)
tracking_write.register_verb("follow_up_resolve", apply_follow_up_resolve)
tracking_write.register_verb("follow_up_dismiss", apply_follow_up_dismiss)
