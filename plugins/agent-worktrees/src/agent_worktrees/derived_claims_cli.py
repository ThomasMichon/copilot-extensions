"""CLI glue for ``derived`` claims (see :mod:`derived_claims`).

``claims add derived <path> --creator <tool>`` creates the directory with its
marker and journals the claim; ``claims settle <path> --reclaim [--dry-run]``
deletes exactly that one directory after every safety check passes and then
settles its claim; ``claims release <path>`` keeps the directory and drops the
claim through the ordinary release path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from . import derived_claims as dc
from . import output


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--creator",
        default=None,
        dest="derived_creator",
        help="with 'add derived': the producing tool's name, recorded in the "
        "directory's marker and the claim note",
    )
    parser.add_argument(
        "--reclaim",
        action="store_true",
        help="with settle on a derived claim: delete exactly that directory "
        "(after marker/nonce, checkout, Git-tracked, link and live-process "
        "checks) and then settle the claim; any check failing leaves both open",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="with settle --reclaim: report the checks and what would be "
        "deleted without changing anything",
    )


def _fail(args, msg: str, code: int = 1) -> int:
    if getattr(args, "json", False):
        return output._json_error(msg, code)
    output.err(msg)
    return code


def _resolve_owner(args, config) -> tuple[Path | None, str, str | None]:
    """``(record_path, worktree_id, error)`` for the local owner worktree."""
    from . import claims_cli, worktree_identity
    from . import config as cfg

    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = claims_cli._resolve_owner_ref_record_path(owner_ref, config)
        if err:
            return None, wt_id, err
        if rec_path is None:
            return None, wt_id, (
                f"owner-ref {owner_ref} is on another machine; derived claims "
                "are local to the checkout that owns the directory"
            )
    else:
        wt_id = worktree_identity._infer_worktree_id(
            getattr(args, "release_worktree", None), config
        )
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        return None, wt_id, f"worktree not found: {wt_id}"
    return rec_path, wt_id, None


def stored_ref(args, ref: str) -> str:
    """Map a user-supplied path to the matching derived claim's stored ref.

    Derived refs are stored as absolute paths; this lets ``release``/``settle``
    accept a relative or differently-cased spelling. Any other ref (or any
    lookup failure) is returned unchanged.
    """
    from . import config as cfg
    from . import tracking

    try:
        rec_path, _wt_id, err = _resolve_owner(args, cfg.load_config())
        if err or rec_path is None:
            return ref
        claim = dc.find_claim(tracking.load_record(rec_path).resources, dc.canonical_path(ref))
    except Exception:  # noqa: BLE001 - mapping is a convenience only
        return ref
    return claim.ref if claim is not None else ref


def claims_add_derived(args, ref: str) -> int:
    """Create ``ref`` with a marker and journal it as an open derived claim."""
    from . import claim_history, claims_cli, tracking, tracking_write
    from . import config as cfg

    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        readiness = claims_cli._coordination_readiness_for_owner_ref(owner_ref, config)
        if not readiness.ready:
            return claims_cli._emit_coordination_rejection(readiness, json_out=args.json)
    else:
        blocked = claims_cli._require_coordination_readiness(config, json_out=args.json)
        if blocked is not None:
            return blocked
    rec_path, wt_id, err = _resolve_owner(args, config)
    if err:
        return _fail(args, f"claims add derived: {err}")
    record = tracking.load_record(rec_path)
    target = dc.canonical_path(ref)
    creator = getattr(args, "derived_creator", None) or "unknown"
    try:
        nonce, created = dc.prepare_directory(
            target,
            worktree_id=wt_id,
            checkout=record.worktree_path or "",
            resources=record.resources,
            creator=creator,
            now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except (dc.DerivedClaimError, OSError) as exc:
        return _fail(args, f"claims add derived: {exc}")
    payload = {
        "worktree_id": wt_id,
        "yaml_path": str(rec_path),
        "kind": dc.KIND,
        "ref": str(target),
        "note": dc.format_note(creator=creator, nonce=nonce),
        "session_id": claim_history.current_session_id(),
    }
    try:
        result = claims_cli._dispatch_claim("claim_add", payload)
    except tracking_write.ResidentRecoveryHeld as exc:
        if created:
            dc.discard_new_directory(target)
        return tracking_write.refuse_recovery(exc, json_output=args.json)
    except tracking_write.AmbiguousWriteOutcome as exc:
        # The ledger may hold the claim; keep the directory so a reclaim can
        # still prove it rather than leaving a claim with nothing behind it.
        return _fail(args, f"claims add derived: write to {wt_id} is in an unknown state: {exc}")
    if result.get("error") in ("frozen", "rejected"):
        if created:
            dc.discard_new_directory(target)
        return _fail(args, result["message"])
    if args.json:
        output._json_output({
            "worktree_id": wt_id,
            "kind": dc.KIND,
            "ref": str(target),
            "state": result.get("state"),
            "created": created,
            "marker": str(target / dc.MARKER_NAME),
            "creator": creator,
        })
        return 0
    verb = "created and claimed" if created else "re-proved existing claim on"
    print(f"{verb} derived directory {target} on {wt_id}")
    print(f"  settle: claims settle {target} --reclaim [--dry-run]   keep: claims release {target}")
    return 0


def claims_settle_reclaim(args, ref: str) -> int:
    """Delete exactly one derived claim's directory, then settle the claim."""
    from . import claim_history, claims_cli, obligations, tracking, tracking_write
    from . import config as cfg

    rec_path, wt_id, err = _resolve_owner(args, cfg.load_config())
    if err:
        return _fail(args, f"claims settle --reclaim: {err}")
    record = tracking.load_record(rec_path)
    claim = dc.find_claim(record.resources, dc.canonical_path(ref))
    if claim is None:
        return _fail(args, f"claims settle --reclaim: no {dc.KIND} claim with ref {ref} on {wt_id}")
    refusals = dc.reclaim_refusals(record, claim)
    dry_run = bool(getattr(args, "dry_run", False))
    base = {"worktree_id": wt_id, "kind": dc.KIND, "ref": claim.ref, "dry_run": dry_run}
    if refusals:
        if args.json:
            output._json_output({**base, "reclaimed": False, "refusals": refusals,
                                 "error": "reclaim refused; claim left open"})
            return 1
        output.err(f"refusing to reclaim {claim.ref}; the claim stays open:")
        for reason in refusals:
            print(f"    - {reason}")
        print(f"  keep the directory and drop the claim instead: claims release {claim.ref}")
        return 1
    if dry_run:
        if args.json:
            output._json_output({**base, "reclaimed": False, "would_delete": claim.ref})
            return 0
        print(f"would delete {claim.ref} and settle its derived claim on {wt_id} (dry-run)")
        return 0
    removed, detail = dc.remove_directory(Path(claim.ref))
    if not removed:
        return _fail(
            args,
            f"claims settle --reclaim: could not delete {claim.ref} ({detail}); "
            "the claim stays open",
        )
    try:
        result = claims_cli._dispatch_claim(
            "claim_settle",
            {
                "worktree_id": wt_id,
                "yaml_path": str(rec_path),
                "ref": claim.ref,
                "disposition": obligations.RELEASED,
                "session_id": claim_history.current_session_id(),
            },
        )
    except tracking_write.ResidentRecoveryHeld as exc:
        return tracking_write.refuse_recovery(exc, json_output=args.json)
    except tracking_write.AmbiguousWriteOutcome as exc:
        return _fail(args, f"claims settle --reclaim: deleted {claim.ref}, but the settle "
                     f"write to {wt_id} is in an unknown state: {exc}")
    if result.get("error"):
        return _fail(args, f"claims settle --reclaim: deleted {claim.ref}, but settling the "
                     f"claim failed: {result.get('message') or result['error']}")
    if args.json:
        output._json_output({**base, "reclaimed": True, "disposition": obligations.RELEASED})
        return 0
    print(f"deleted {claim.ref} and settled its derived claim on {wt_id} -> released")
    return 0
