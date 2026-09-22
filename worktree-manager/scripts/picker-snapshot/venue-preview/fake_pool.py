#!/usr/bin/env python3
"""Hermetic stand-in for ``agent-codespaces pool --picker-json`` (real:
``plugins/agent-codespaces/src/agent_codespaces/pool.py``'s ``picker_payload``),
used ONLY to drive the ``picker-venue-pivots`` effort's before/after preview
renders (``efforts/active/picker-venue-pivots``).

The fixture rows already carry the fields a Phase 1-implemented ``pool.py``
would compute: a composed line-two string (``subtitle``) in the vision's
``"[mark] <durable title> - <transient activity>"`` grammar, a
``claims_summary`` (the shared claims-pecking-order module's "1-2 prominent"
output), and a ``driven`` stat (the reserved driving-worktree indicator). The
**current** manifest (``agent-codespaces.current.json``, a byte-for-byte copy
of the real ``plugins/agent-codespaces/pivots/agent-codespaces.json``) simply
never maps most of these -- rendering the SAME rows through it is what proves
the "dropped field" gap the effort's Phase 1 fixes. The **proposed** manifest
(``agent-codespaces.proposed.json``) maps them all.

Never talks to a real CodeSpace, git remote, or agent-bridge -- accepts (and
ignores) ``--machine``/``--picker-json`` exactly like the real CLI so the same
pivot manifest ``list`` template works unmodified.
"""
from __future__ import annotations

import argparse
import json
import sys

ROWS = [
    {
        "id": "cs-a1c4-relay",
        "name": "cs-a1c4-relay",
        "display": "cs-a1c4-relay",
        "group": "sample-repo @ acme-org",
        "status": "RUNNING",
        "worktree": "a1c4",
        "worktree_title": "Reproduce #4021 on a clean box",
        # The composed line-two string a Phase-1 pool.py would emit: a
        # declared checkout intent (durable title) + " - " + the most
        # recent agent-bridge-reported activity (transient).
        "subtitle": (
            "\u2192 Reproduce #4021 on a clean box - "
            "fixing the relay reconnect backoff, running the flaky-relay repro"
        ),
        "repository": "acme-org/sample-repo",
        "repo": "sample-repo",
        "branch": "users/alex/fix-relay-backoff",
        "account": "alex-operator",
        "disposition": "in-use",
        "cores": "4",
        "health": "running",
        "occupancy": "in-use",
        "safe": "no",
        "driven": "DRIVEN",
        "claims_summary": "PR #2481",
        "live": "yes",
        "worktree_status": {
            "title": "Worktree a1c4 \u2014 session status",
            "body": (
                "# Worktree a1c4\n\n"
                "**Branch:** `worktree/build-host-1-20260916-140200-a1c4`"
                "  \u00b7  **Driving:** cs-a1c4-relay (CodeSpace)\n\n"
                "## Status\n"
                "- Turns: 18\n"
                "- Commits: 3 (2 pushed, 1 local)\n"
                "- Claims: 1 (`pr#2481`)\n"
                "- Live: active (last snagged: relay reconnect backoff fix)\n"
            ),
        },
    },
    {
        "id": "cs-88de-mcp",
        "name": "cs-88de-mcp",
        "display": "cs-88de-mcp",
        "group": "sample-repo @ acme-org",
        "status": "RUNNING",
        "worktree": "88de",
        "worktree_title": "Fix agent-mcp decorator ordering regression",
        # No live agent-bridge session right now -- durable title only, no
        # " - <activity>" half (graceful-absence, not a placeholder).
        "subtitle": "\u2192 Fix agent-mcp decorator ordering regression",
        "repository": "acme-org/sample-repo",
        "repo": "sample-repo",
        "branch": "users/alex/mcp-decorator-fix",
        "account": "alex-operator",
        "disposition": "in-use",
        "cores": "4",
        "health": "running",
        "occupancy": "in-use",
        "safe": "unknown",
        "driven": "DRIVEN",
        "claims_summary": "PR #2477 \u00b7 bug #2410",
        "live": "",
        "worktree_status": {
            "title": "Worktree 88de \u2014 session status",
            "body": (
                "# Worktree 88de\n\n"
                "**Branch:** `worktree/build-host-1-20260916-090000-88de`"
                "  \u00b7  **Driving:** cs-88de-mcp (CodeSpace)\n\n"
                "## Status\n"
                "- Turns: 34\n"
                "- Commits: 5 (all local, not yet pushed)\n"
                "- Claims: 2 (`pr#2477` draft, `bug#2410` linked)\n"
                "- Live: idle (no session currently attached)\n"
            ),
        },
    },
    {
        "id": "cs-9f02-idle",
        "name": "cs-9f02-idle",
        "display": "cs-9f02-idle",
        "group": "sample-repo @ acme-org",
        "status": "STOPPED",
        "worktree": "",
        "worktree_title": "",
        # No driving worktree at all -- falls back to bare venue identity
        # (repo + branch), the row grammar's last resort for the durable
        # title, and no mark/activity.
        "subtitle": "sample-repo @ users/jordan/spike-auth-flow",
        "repository": "acme-org/sample-repo",
        "repo": "sample-repo",
        "branch": "users/jordan/spike-auth-flow",
        "account": "jordan-operator",
        "disposition": "idle",
        "cores": "2",
        "health": "stopped",
        "occupancy": "free",
        "safe": "yes",
        "driven": "",
        "claims_summary": "",
        "live": "",
    },
    {
        "id": "cs-c72e-stale",
        "name": "cs-c72e-stale",
        "display": "cs-c72e-stale",
        "group": "sample-harness @ acme-org",
        "status": "STALE",
        "worktree": "c72e",
        "worktree_title": "Investigate augloop-workflows flaky scenario runner",
        "subtitle": (
            "\u26a0 Investigate augloop-workflows flaky scenario runner - "
            "holder worktree gone (orphaned lock)"
        ),
        "repository": "acme-org/sample-harness",
        "repo": "sample-harness",
        "branch": "users/alex/flaky-runner",
        "account": "alex-operator",
        "disposition": "stale",
        "cores": "2",
        "health": "stopped",
        "occupancy": "orphan",
        "safe": "unknown",
        "driven": "",
        "claims_summary": "issue #118",
        "live": "",
    },
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fake-agent-codespaces-pool")
    ap.add_argument("subcommand", nargs="?")
    ap.add_argument("--picker-json", action="store_true")
    ap.add_argument("--machine")
    ap.parse_args(argv)
    payload = {
        "entries": ROWS,
        "summary": {
            "spent_cores": 10,
            "total_cores": 16,
            "headroom_cores": 6,
            "running_count": 2,
            "total_count": 4,
            "note": "",
        },
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
