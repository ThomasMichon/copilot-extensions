"""``agent-worktrees pause`` CLI surface -- see :mod:`pause` for the full
contract. Kept a separate module from :mod:`finalize_cli` per this repo's
module-size cap convention (mirrors the ``claims_transitive_cli`` split)."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import config as cfg, output, pause as pause_mod


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "pause",
        help="Sync + tidy a worktree without finalizing it: settle provably-done "
        "claims, report what's still genuinely open, and stop -- never errors "
        "on an open claim, never touches the worktree directory/branch.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument(
        "--worktree-id",
        dest="worktree_id_flag",
        default=None,
        help="Worktree ID to pause (explicit automation form)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)


def cmd_pause(args: argparse.Namespace) -> int:
    core = _core()
    use_json = getattr(args, "json", False)
    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        try:
            config = cfg.load_config(Path(args.config) if args.config else None)
        except Exception as e:
            if use_json:
                return core._json_error(str(e))
            raise
        positional_id = getattr(args, "worktree_id", None)
        flagged_id = getattr(args, "worktree_id_flag", None)
        if positional_id and flagged_id and positional_id != flagged_id:
            msg = "Conflicting worktree IDs: positional worktree-id and --worktree-id must match."
            if use_json:
                return core._json_error(msg, 2)
            output.err(msg)
            return 2
        worktree_id = core._infer_worktree_id(flagged_id or positional_id, config)
        if not worktree_id:
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return core._json_error(msg)
            output.err(msg)
            return 1
        worktree_id = core._resolve_worktree_id(worktree_id)

        result = pause_mod.pause_worktree(worktree_id, config, dry_run=args.dry_run)

        if use_json:
            core._json_output(
                {
                    "worktree_id": result.worktree_id,
                    "synced": result.synced,
                    "settled": result.settled,
                    "remaining": result.remaining,
                    "clean": result.clean,
                }
            )
            return 0 if result.synced else 1

        if not result.synced:
            # sync_forward already printed the specific blocker (dirty tree,
            # conflict, missing upstream); pause stops there rather than
            # reporting a claim-ledger snapshot against a branch state that
            # didn't actually move.
            return 1

        if result.settled:
            output.ok(f"Settled {len(result.settled)} provably-resolved claim(s):")
            for c in result.settled:
                print(f"    - {c['kind']}: {c['ref']} -> {c['state']}")

        if not result.remaining:
            output.ok(
                f"Worktree {result.worktree_id} is clean -- "
                "'agent-worktrees finalize' would now succeed."
            )
            return 0

        output.warn(
            f"Worktree {result.worktree_id} paused with "
            f"{len(result.remaining)} claim(s) still genuinely open:"
        )
        for c in result.remaining:
            note = f"  -- {c['note']}" if c.get("note") else ""
            print(f"    - {c['kind']}: {c['ref']}{note}")
        print(
            "  Leave these open if the work is intentionally still in "
            "flight, or settle one with 'agent-worktrees claims settle "
            "<ref>' / release it, then re-run pause or finalize."
        )
        return 0
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
