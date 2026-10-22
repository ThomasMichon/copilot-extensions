"""Defer only a typed Bridge-owned seed using existing launch-seed receipts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config, launch_seed_state, output, worktree_identity


def claim(args, record_path: Path | None = None) -> dict:
    operation = getattr(args, "bridge_seed_operation", None)
    if not operation:
        return {}
    if not getattr(args, "bridge_scope_id", None) or getattr(args, "seed", None):
        raise ValueError("Deferred Bridge seed requires its scope and no TTY seed")
    receipt = {"seed": None}
    if record_path is not None:
        current = launch_seed_state.pending(record_path)
        if current is not None and current.kind == "new":
            if current.handoff_id:
                if not current.handoff_id.startswith("bridge-"):
                    raise ValueError("A legacy creation seed may already have been typed; refusing to resend")
                operation = current.handoff_id.removeprefix("bridge-")
            receipt = launch_seed_state.take(
                record_path, seed_id=current.seed_id, kind="new", handoff_id="bridge-" + operation,
            )
            if not receipt.get("seed"):
                raise ValueError("Creation seed changed while deferring Bridge delivery")
    return {
        "bridge_seed_deferred": True, "bridge_seed_operation": operation,
        "deferred_creation_seed": receipt,
    }


def settle(args) -> int:
    payload = json.loads(sys.stdin.read())
    handoff = payload["receipt"]["seed"].get("handoff_id")
    if not isinstance(handoff, str) or not handoff.startswith("bridge-"):
        raise ValueError("Settlement requires the Bridge-deferred creation receipt")
    identity = worktree_identity._resolve_worktree_id(args.worktree_id)
    path = config.tracking_dir() / f"{identity}.yaml"
    result = launch_seed_state.finish(path, payload["receipt"])
    output._json_output(result)
    return 0 if result.get("finished") else 1
