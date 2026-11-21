"""Owner-qualified settlement through the guarded resident claim writer."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import claim_history, config as cfg, obligations, output, worktree_identity


def settle(
    args: argparse.Namespace, ref: str, *,
    dispatch_claim: Callable[[str, dict[str, Any]], dict[str, Any]],
    resolve_owner_ref: Callable[[str, cfg.Config], tuple[Path | None, str, str | None]],
) -> int:
    """Settle one exact outbound resource without bypassing its native owner."""
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    pins = {
        key: getattr(args, key, None)
        for key in ("expected_record_nonce", "expected_claim_nonce", "expected_kind")
    }
    if any(value is not None for value in pins.values()):
        if not owner_ref or not all(pins.values()):
            message = "claims settle: all three identity pins and a qualified --owner-ref are required"
            if args.json:
                return output._json_error(message, 2)
            output.err(message)
            return 2
    else:
        pins = {}
    wt_id: str | None
    if owner_ref:
        rec_path, wt_id, err = resolve_owner_ref(owner_ref, config)
        if err:
            if args.json:
                return output._json_error(err, 2)
            output.err(err)
            return 2
        if rec_path is None:
            if args.json:
                output._json_output({
                    "worktree_id": wt_id, "ref": ref, "deferred": True,
                    "reason": "cross-machine-owner",
                })
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
    from . import tracking_write

    try:
        result = dispatch_claim("claim_settle", {
            "worktree_id": wt_id, "yaml_path": str(rec_path), "ref": ref,
            "disposition": disposition, "session_id": claim_history.current_session_id(),
            **pins,
        })
    except tracking_write.ResidentRecoveryHeld as exc:
        return tracking_write.refuse_recovery(exc, json_output=args.json)
    except tracking_write.AmbiguousWriteOutcome as exc:
        msg = f"claims settle: write to {wt_id} is in an unknown state: {exc}"
        if args.json:
            return output._json_error(msg)
        output.err(msg)
        return 1
    if result.get("error") in {"reserved", "rejected", "frozen"}:
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
