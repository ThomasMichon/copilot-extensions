#!/usr/bin/env python3
"""Hermetic stand-in for ``agent-containers fleet --json`` (real:
``plugins/agent-containers/src/agent_containers/__main__.py``'s
``_cmd_fleet``), used ONLY to drive the ``picker-venue-pivots`` effort's
before/after preview renders (``efforts/active/picker-venue-pivots``).

Mirrors ``fake_pool.py``'s pattern: fixture rows already carry the fields a
Phase 2-implemented ``_cmd_fleet`` would compute (a ``lease``-derived
``worktree`` cross-link, a composed line-two ``subtitle``, a
``claims_summary``, a compact ``sess`` liveness stat -- see ``fake_pool.py``'s
own docstring for why this is a narrow multi-valued column, not a "driven"
boolean) -- rendering the SAME rows through the **current** manifest
(``agent-containers.current.json``, a byte-for-byte copy of the real
``plugins/agent-containers/pivots/agent-containers.json``) proves today's thin
badge-list shape (only ``name``/``image``/``state``/``fleet`` ever render);
rendering them through the **proposed** manifest
(``agent-containers.proposed.json``) shows the Codespaces-parity target.

Never talks to a real Docker daemon -- accepts (and ignores) ``--json``
exactly like the real CLI so the same pivot manifest ``list`` template works
unmodified. Real field names per ``test_fleet_json.py``'s fixture contract:
``name``/``container_id``/``image``/``state``/``status``/``fleet``/
``local_folder``/``lease``/``security_profile``/``network``/etc.
"""
from __future__ import annotations

import argparse
import json
import sys

ROWS = [
    {
        "name": "sample-repo-1",
        "container_id": "cid-sample-repo-1",
        "image": "ghcr.io/acme-org/sample-repo-dev:latest",
        "state": "running",
        "status": "Up 2 hours",
        "fleet": "sample-repo",
        "local_folder": "/work/sample-repo",
        "lease": "a1c4",
        "worktree_title": "Reproduce #4021 on a clean box",
        "subtitle": (
            "\u2192 Reproduce #4021 on a clean box - "
            "fixing the relay reconnect backoff, running the flaky-relay repro"
        ),
        "security_profile": "trusted",
        "network": "bridge",
        "claims_summary": "PR #2481",
        "sess": "LIVE",
        "worktree_status": {
            "title": "Worktree a1c4 \u2014 session status",
            "body": (
                "# Worktree a1c4\n\n"
                "**Driving:** sample-repo-1 (fleet container)\n\n"
                "## Status\n"
                "- Turns: 18\n"
                "- Commits: 3 (2 pushed, 1 local)\n"
                "- Claims: 1 (`pr#2481`)\n"
                "- Live: active (last snagged: relay reconnect backoff fix)\n"
            ),
        },
    },
    {
        "name": "sample-repo-2",
        "container_id": "cid-sample-repo-2",
        "image": "ghcr.io/acme-org/sample-repo-dev:latest",
        "state": "running",
        "status": "Up 40 minutes",
        "fleet": "sample-repo",
        "local_folder": "/work/sample-repo",
        "lease": "",
        "worktree_title": "",
        "subtitle": "sample-repo fleet \u00b7 ghcr.io/acme-org/sample-repo-dev:latest",
        "security_profile": "trusted",
        "network": "bridge",
        "claims_summary": "",
        "sess": "",
    },
    {
        "name": "sample-harness-1",
        "container_id": "cid-sample-harness-1",
        "image": "ghcr.io/acme-org/sample-harness-dev:latest",
        "state": "exited",
        "status": "Exited (0) 3 days ago",
        "fleet": "sample-harness",
        "local_folder": "/work/sample-harness",
        "lease": "c72e",
        "worktree_title": "Investigate augloop-workflows flaky scenario runner",
        "subtitle": (
            "\u26a0 Investigate augloop-workflows flaky scenario runner - "
            "holder worktree gone (orphaned lock)"
        ),
        "security_profile": "restricted",
        "network": "none",
        "claims_summary": "issue #118",
        "sess": "",
    },
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fake-agent-containers-fleet")
    ap.add_argument("subcommand", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.parse_args(argv)
    # Real `_cmd_fleet --json` emits a BARE top-level array (see
    # test_fleet_json.py), unlike pool.picker_payload's {"entries": [...]}
    # envelope -- kept identical here so the proposed manifest's `list`
    # template needs no reshaping beyond what a real Phase 2 CLI would emit.
    json.dump(ROWS, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
