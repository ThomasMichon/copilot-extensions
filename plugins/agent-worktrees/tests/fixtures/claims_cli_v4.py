"""Version-4 release/settle callers from 16f10b7fe867.

Imports are adapted for fixture loading; the caller functions are unchanged.
"""

from agent_worktrees.claims_cli import (
    argparse, cfg, worktree_identity, output, claim_history, obligations,
    _dispatch_claim, _resolve_owner_ref_record_path,
)

def _claims_release(args: argparse.Namespace, ref: str) -> int:
    """Retire a single outbound resource claim by ref from a worktree's record."""
    config = cfg.load_config()
    wt_id = worktree_identity._infer_worktree_id(getattr(args, "release_worktree", None), config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return output._json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    from agent_worktrees import tracking_write

    try:
        result = _dispatch_claim(
            "claim_release",
            {
                "worktree_id": wt_id,
                "yaml_path": str(rec_path),
                "ref": ref,
                "remove": bool(getattr(args, "remove", False)),
                "session_id": claim_history.current_session_id(),
            },
        )
    except tracking_write.ResidentRecoveryHeld as exc:
        return tracking_write.refuse_recovery(exc, json_output=args.json)
    except tracking_write.AmbiguousWriteOutcome as exc:
        msg = f"claims release: write to {wt_id} is in an unknown state: {exc}"
        if args.json:
            return output._json_error(msg)
        output.err(msg)
        return 1
    if result.get("error") == "not_found":
        if args.json:
            return output._json_error(f"no outbound claim with ref: {ref}")
        output.err(f"no outbound claim with ref: {ref} on {wt_id}")
        return 1
    if result.get("error") in {"reserved", "rejected"}:
        if args.json:
            return output._json_error(result["message"])
        output.err(result["message"])
        return 1
    action = result["action"]
    if args.json:
        output._json_output({"worktree_id": wt_id, "ref": ref, "action": action})
        return 0
    print(f"{action} outbound claim {ref} on {wt_id}")
    return 0


def _claims_settle(args: argparse.Namespace, ref: str) -> int:
    """Settle one outbound resource claim's disposition by ref."""
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = _resolve_owner_ref_record_path(owner_ref, config)
        if err:
            if args.json:
                return output._json_error(err, 2)
            output.err(err)
            return 2
        if rec_path is None:
            if args.json:
                output._json_output(
                    {
                        "worktree_id": wt_id,
                        "ref": ref,
                        "deferred": True,
                        "reason": "cross-machine-owner",
                    }
                )
                return 0
            output.warn(
                f"owner-ref {owner_ref} is on another machine -- settle deferred "
                f"to the lease mirror (no local ledger write)"
            )
            return 0
    else:
        wt_id = worktree_identity._infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return output._json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    disposition = obligations.RELEASED if getattr(args, "released", False) else obligations.AT_REST
    from agent_worktrees import tracking_write

    try:
        result = _dispatch_claim(
            "claim_settle",
            {
                "worktree_id": wt_id,
                "yaml_path": str(rec_path),
                "ref": ref,
                "disposition": disposition,
                "session_id": claim_history.current_session_id(),
            },
        )
    except tracking_write.ResidentRecoveryHeld as exc:
        return tracking_write.refuse_recovery(exc, json_output=args.json)
    except tracking_write.AmbiguousWriteOutcome as exc:
        msg = f"claims settle: write to {wt_id} is in an unknown state: {exc}"
        if args.json:
            return output._json_error(msg)
        output.err(msg)
        return 1
    if result.get("error") in {"reserved", "rejected"}:
        if args.json:
            return output._json_error(result["message"])
        output.err(result["message"])
        return 1
    if result.get("error") == "not_found":
        if args.json:
            return output._json_error(f"no outbound claim with ref: {ref}")
        output.err(f"no outbound claim with ref: {ref} on {wt_id}")
        return 1
    if args.json:
        output._json_output({"worktree_id": wt_id, "ref": ref, "disposition": disposition})
        return 0
    print(f"settled outbound claim {ref} on {wt_id} -> {disposition}")
    return 0
