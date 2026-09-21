"""agent-worktrees reconcile -- out-of-band PR-state refresh across every
tracked worktree, including ``finalized`` (agent-worktrees-fleet-flows Phase
2, #2740).

Manual dispatch from ``__main__.py`` (mirrors ``fleet``/``lease``'s own
top-level verb pattern -- ``__main__.py`` is at its grandfathered module-size
ceiling) so this module owns its own argparse and never grows it.

Supersedes the *render-time* PR reconcile (#2102): ``pr-status``/``pr-ready``/
``create_pr`` each reconcile the active PR against the provider, but only
when one of those paths actually runs against a live session -- a
``finalized`` worktree (or any worktree nobody is currently in) never gets
that call, so its stale ``open`` tag never heals. This verb reconciles every
tracked record directly, out of band, so the Picker's ``list`` stays a pure
file read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from . import config as cfg
from . import tracking

# ``--worktree-id`` is a raw user-supplied string, not resolved through any
# registry -- a value like ``../../etc`` must not be allowed to escape
# ``cfg.tracking_dir()`` (mirrors ``handoff_trace._UNSAFE_COMPONENT``).
_UNSAFE_COMPONENT = re.compile(r"[/\\\0]|^\.\.?$")


def run_reconcile(argv: list[str]) -> int:
    """Entry point for the ``reconcile`` verb (manual dispatch from ``__main__.py``)."""
    parser = argparse.ArgumentParser(
        prog="agent-worktrees reconcile",
        description="Force-refresh PR state (heals a stale terminal state and "
        "a number-less record, #2146/#2102) for every tracked worktree, "
        "including 'finalized' ones -- out of band, so 'list' stays a pure "
        "file read.",
    )
    parser.add_argument(
        "--worktree-id", default=None,
        help="Reconcile only this worktree (default: every tracked worktree "
        "on this machine, any status)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output (default: human summary)")
    args = parser.parse_args(argv)

    try:
        config = cfg.load_config()
    except Exception as exc:
        print(f"agent-worktrees reconcile: could not load config: {exc}", file=sys.stderr)
        return 1

    tracking_path = cfg.tracking_dir()
    if args.worktree_id:
        if _UNSAFE_COMPONENT.search(args.worktree_id):
            message = f"invalid --worktree-id: {args.worktree_id!r}"
            if args.json:
                print(json.dumps({"error": message}, default=str))
            else:
                print(f"agent-worktrees reconcile: {message}", file=sys.stderr)
            return 1
        yaml_path = tracking_path / f"{args.worktree_id}.yaml"
        if not yaml_path.exists():
            message = f"No tracking record found for '{args.worktree_id}'."
            if args.json:
                print(json.dumps({"error": message}, default=str))
            else:
                print(f"agent-worktrees reconcile: {message}", file=sys.stderr)
            return 1
        records = [tracking.load_record(yaml_path)]
    else:
        # No status_filter: every tracked worktree, including 'finalized' --
        # the whole point of superseding the render-time reconcile (#2102),
        # which only ever ran against a worktree with a live session.
        records = tracking.list_records(tracking_path)

    from . import pr_reconcile

    changed: list[str] = []
    errors: list[dict] = []
    for record in records:
        try:
            if pr_reconcile.reconcile_pr_state(record, config):
                changed.append(record.worktree_id)
        except Exception as exc:
            errors.append({"worktree_id": record.worktree_id, "error": str(exc)})

    result = {"checked": len(records), "changed": changed, "errors": errors}
    if args.json:
        print(json.dumps(result, default=str))
        return 0

    print(f"reconcile: checked {len(records)} worktree(s), {len(changed)} changed.")
    for worktree_id in changed:
        print(f"  - {worktree_id}: PR state updated")
    for err in errors:
        print(f"  ! {err['worktree_id']}: {err['error']}")
    return 0
