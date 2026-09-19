#!/usr/bin/env python3
"""Hermetic stand-in for ``agent-dispatch-board`` (real script:
``plugins/agent-dispatch/src/agent_dispatch/board_cli.py``), used ONLY to
drive the Tasks-pane UX-overhaul preview renders. Prints a fixed, deterministic
JSON array shaped like the real board's rows plus the NEW fields this effort
proposes (``target_worktree`` already exists on the real board; ``wt_turns``/
``wt_live``/``wt_disposition``/``artifacts_summary``/``charter``/
``worktree_status``/``has_worktree``/``embodied`` are proposed additions).

``cli_openable`` is the proposed, board-computed gate for the "Open into a
CLI session" action: true only for Proposed/Queued-with-no-pool/Suspended
tasks. An embodied task with a LIVE headless agent (Blocked or Started) is
never CLI-openable -- CLI and ACP cannot co-drive the same session -- and a
Queued task already claimed by a pool is excluded too (the pool's own next
worker should take it, not the operator's terminal). This is deliberately
computed server-side (not expressed as pivot-manifest ``when`` boilerplate)
since it is a conditional-by-branch rule, not a flat field-equality gate.

Opening into a CLI session is a genuinely DIFFERENT embodiment path from a
headless kick, not "spawn the pool's dedicated worker, then attach a
terminal": it never routes through the pool or applies a named worker
identity (``worker_identities.py``)'s fixed acting theme/focus/rules, since
those rails exist to keep an UNATTENDED headless agent on target -- exactly
what the operator does not want here. A CLI-opened session gets only the
succinct ``--interactive`` context prompt (charter + phase + task-state
tooling); everything past that is the operator's own direct, open-ended
control.

Never talks to a real coordinator -- accepts (and ignores) ``--machine`` /
``--recent-mins`` / ``--label`` / ``--limit`` exactly like the real CLI so the
same pivot manifest ``list`` template works unmodified.
"""
from __future__ import annotations

import argparse
import json
import sys

# GROUPS order mirrors board_cli.GROUPS exactly (Blocked first -- needs the
# operator soonest).
ROWS = [
    {
        "id": "task-9f21",
        "title": "Review draft: harden the relay reconnect path",
        "status": "suspended",
        "group": "Blocked",
        "awaiting_steer": True,
        "repo": "git@github.com:acme-org/sample-repo.git",
        "repo_name": "sample-repo",
        "target_worktree": "a1c4",
        "turn_count": 18,
        "activity": "STALLED",
        "has_worktree": "true",
        "embodied": "true",
        "cli_openable": "false",
        "wt_turns": 18,
        "wt_live": "idle 6m",
        "wt_disposition": "DIRTY",
        "artifacts_summary": "PR #2481",
        "card": {
            "title": "Review draft: PR 2481 — harden the relay reconnect path",
            "status": "Recommended verdict: Approve",
            "link": "https://github.com/acme-org/sample-repo/pull/2481",
            "body": (
                "# Review of PR 2481 — harden the relay reconnect path\n\n"
                "> *Recommended verdict:* **APPROVE**\n\n"
                "## Proposed comment\n\n"
                "> The reconnect backoff now caps at 30s and preserves the "
                "in-flight request queue across a reconnect. Looks correct; "
                "one nit below.\n>\n"
                "> *AI-assisted review comment.*\n\n"
                "*Rationale: exercised the reconnect path against a "
                "simulated flaky relay in `test_bridge_liveness_probe.py`; "
                "no dropped requests observed.*\n"
            ),
            "request_input": [
                {"name": "comments", "type": "choice",
                 "options": ["Accept", "Reject"]},
                {"name": "reason", "type": "textarea",
                 "show_when": {"field": "comments", "equals": "Reject"}},
                {"name": "verdict", "type": "choice",
                 "options": ["Approve", "Waiting for author", "Reject"],
                 "show_when": {"field": "comments", "equals": "Accept"}},
            ],
        },
        "charter": {
            "title": "Charter — task-9f21",
            "body": (
                "# Review draft: harden the relay reconnect path\n\n"
                "**Repo:** sample-repo · **Registrar:** "
                "`pr-review-webhook` · **Phase:** Blocked (awaiting steer)\n\n"
                "## Description\n"
                "A GitHub PR-review webhook proposed this task after "
                "`agent-bridge`'s relay reconnect logic changed. The worker "
                "drafted a review and is waiting for the operator's verdict.\n\n"
                "## Structured metadata\n"
                "- `source`: github-pr-review-webhook\n"
                "- `pr`: acme-org/sample-repo#2481\n"
                "- `max_attempts`: 3 (1 used)\n"
                "- `created`: 2026-09-16T14:02:00Z\n"
            ),
        },
        "worktree_status": {
            "title": "Worktree a1c4 — session status",
            "body": (
                "# Worktree a1c4\n\n"
                "**Branch:** `worktree/build-host-1-20260916-140200-a1c4`"
                "  ·  **Disposition:** DIRTY (uncommitted changes)\n\n"
                "## Session lineage\n"
                "1. `0f4b6106…` (spawned by task-9f21, headless bridge agent)\n\n"
                "## Status\n"
                "- Turns: 18\n"
                "- Commits: 3 (2 pushed, 1 local)\n"
                "- Claims: 2 (`pr#2481`, `issue#2455` follow-up)\n"
                "- Live: idle 6m (last tool call: `gh pr view`)\n\n"
                "## Claims\n"
                "- `pr` → acme-org/sample-repo#2481 (open, review requested)\n"
                "- `issue` → acme-org/sample-repo#2455 (follow-up filed)\n"
            ),
        },
    },
    {
        "id": "task-7b03",
        "title": "Fix agent-mcp decorator ordering regression",
        "status": "started",
        "group": "Started",
        "awaiting_steer": False,
        "repo": "git@github.com:acme-org/sample-repo.git",
        "repo_name": "sample-repo",
        "target_worktree": "88de",
        "turn_count": 34,
        "activity": "ACTIVE",
        "has_worktree": "true",
        "embodied": "true",
        "cli_openable": "false",
        "wt_turns": 34,
        "wt_live": "live · 1 client",
        "wt_disposition": "WIP",
        "artifacts_summary": "PR #2477 · bug #2410",
        "charter": {
            "title": "Charter — task-7b03",
            "body": (
                "# Fix agent-mcp decorator ordering regression\n\n"
                "**Repo:** sample-repo · **Registrar:** "
                "`repository-issue-loop` · **Phase:** Started\n\n"
                "## Description\n"
                "Filed from issue #2410 (decorator stack applies handlers "
                "out of order after the 0.2.0 refactor). Embodied on worktree "
                "88de; a headless bridge agent is actively working it.\n"
            ),
        },
        "worktree_status": {
            "title": "Worktree 88de — session status",
            "body": (
                "# Worktree 88de\n\n"
                "**Branch:** `worktree/build-host-1-20260916-090000-88de`"
                "  ·  **Disposition:** WIP\n\n"
                "## Session lineage\n"
                "1. `3ac910f2…` (spawned by task-7b03)\n"
                "2. `9e1204ab…` (resumed after a handoff, turn 21)\n\n"
                "## Status\n"
                "- Turns: 34\n"
                "- Commits: 5 (all local, not yet pushed)\n"
                "- Claims: 2 (`pr#2477` draft, `bug#2410` linked)\n"
                "- Live: attached, 1 client\n"
            ),
        },
    },
    {
        "id": "task-4410",
        "title": "Nightly agent-index corpus reindex",
        "status": "queued",
        "group": "Queued",
        "awaiting_steer": False,
        "repo": "git@github.com:acme-org/sample-repo.git",
        "repo_name": "sample-repo",
        "target_worktree": None,
        "turn_count": 0,
        "activity": None,
        "has_worktree": "false",
        "embodied": "false",
        "pool": "agent-index-schedule",
        "cli_openable": "false",
        "artifacts_summary": "—",
        "charter": {
            "title": "Charter — task-4410",
            "body": (
                "# Nightly agent-index corpus reindex\n\n"
                "**Repo:** sample-repo · **Registrar:** "
                "`agent-index-schedule` · **Phase:** Queued\n\n"
                "## Description\n"
                "A scheduled (cron) registration; approved and waiting for a "
                "free worker slot. No worktree assigned yet. Assigned to the "
                "`agent-index-schedule` pool, so it is **not** offered for an "
                "interactive CLI open — a pooled task's next worker is "
                "whichever slot the pool assigns, not the operator's terminal.\n"
            ),
        },
    },
    {
        "id": "task-2201",
        "title": "Draft: extend related.yaml with sunshine's new locus",
        "status": "proposed",
        "group": "Proposed",
        "awaiting_steer": False,
        "repo": "git@github.com:alex-operator/dotfiles.git",
        "repo_name": "dotfiles",
        "target_worktree": None,
        "turn_count": 0,
        "activity": None,
        "has_worktree": "false",
        "embodied": "false",
        "pool": None,
        "cli_openable": "true",
        "artifacts_summary": "—",
        "charter": {
            "title": "Charter — task-2201",
            "body": (
                "# Draft: extend related.yaml with sunshine's new locus\n\n"
                "**Repo:** dotfiles · **Registrar:** `manual` · "
                "**Phase:** Proposed\n\n"
                "## Description\n"
                "Not yet approved. Awaiting operator review before it "
                "enters the queue. No pool assigned — opening this into a "
                "CLI session takes it interactively instead of dispatching "
                "it headlessly, and never through the pool or a named "
                "worker identity: no acting-theme rails, just this task's "
                "charter as context and the operator's own direct control.\n"
            ),
        },
    },
    {
        "id": "task-6650",
        "title": "Investigate augloop-workflows flaky scenario runner",
        "status": "suspended",
        "group": "Suspended",
        "awaiting_steer": False,
        "repo": "git@github.com:acme-org/sample-harness.git",
        "repo_name": "sample-harness",
        "target_worktree": "c72e",
        "turn_count": 9,
        "activity": None,
        "has_worktree": "true",
        "embodied": "true",
        "paused_by_user": "true",
        "cli_openable": "true",
        "wt_turns": 9,
        "wt_live": "suspended",
        "wt_disposition": "WIP",
        "artifacts_summary": "issue #118",
        "charter": {
            "title": "Charter — task-6650",
            "body": (
                "# Investigate augloop-workflows flaky scenario runner\n\n"
                "**Repo:** sample-harness · **Registrar:** "
                "`repository-issue-loop` · **Phase:** Suspended (user-paused)\n\n"
                "## Description\n"
                "The operator explicitly **paused** this task from the "
                "Picker: the assigned agent is suspended and agent-dispatch "
                "will not re-queue it until unpaused.\n"
            ),
        },
        "worktree_status": {
            "title": "Worktree c72e — session status",
            "body": (
                "# Worktree c72e\n\n"
                "**Disposition:** WIP (paused mid-session)\n\n"
                "## Session lineage\n"
                "1. `77bf20aa…` (spawned by task-6650, suspended by operator "
                "pause at turn 9)\n\n"
                "## Status\n"
                "- Turns: 9\n"
                "- Commits: 1 (local)\n"
                "- Claims: 1 (`issue#118`)\n"
                "- Live: suspended (paused by user 2026-09-16T22:40Z)\n"
            ),
        },
    },
    {
        "id": "task-0099",
        "title": "Add compression flag to the SSH tunnel",
        "status": "completed",
        "group": "Completed",
        "awaiting_steer": False,
        "repo": "git@github.com:acme-org/sample-repo.git",
        "repo_name": "sample-repo",
        "target_worktree": None,
        "turn_count": 22,
        "activity": None,
        "has_worktree": "false",
        "embodied": "false",
        "cli_openable": "false",
        "artifacts_summary": "PR #2401 (merged)",
        "charter": {
            "title": "Charter — task-0099",
            "body": "# Add compression flag to the SSH tunnel\n\n**Phase:** Completed\n",
        },
    },
    {
        "id": "task-8823",
        "title": "Spike: WSL2 vsock transport for agent-bridge",
        "status": "abandoned",
        "group": "Abandoned",
        "awaiting_steer": False,
        "repo": "git@github.com:acme-org/sample-repo.git",
        "repo_name": "sample-repo",
        "target_worktree": None,
        "turn_count": 4,
        "activity": None,
        "has_worktree": "false",
        "embodied": "false",
        "cli_openable": "false",
        "artifacts_summary": "—",
        "charter": {
            "title": "Charter — task-8823",
            "body": (
                "# Spike: WSL2 vsock transport for agent-bridge\n\n"
                "**Phase:** Abandoned — force-abandoned by the operator from "
                "the Picker (\"not now\").\n"
            ),
        },
    },
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fake-agent-dispatch-board")
    ap.add_argument("--machine", required=True)
    ap.add_argument("--recent-mins", type=int, default=120)
    ap.add_argument("--label")
    ap.add_argument("--limit", type=int, default=200)
    ap.parse_args(argv)
    json.dump(ROWS, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
