"""``agent-bridge restart-worktree`` -- CLI wrapper over ``POST
/api/v1/worktrees/{id}/restart``.

Extracted into its own module (``__main__.py`` is at its grandfathered
module-size ceiling) rather than inlined there, mirroring this plugin's own
precedent (``worktree_probe.py``, ``session_host_liveness.py``).

Stops a worktree's interactive (mux-launched) Copilot in place and, on
success, expires its live-session registration server-side (#2906) -- the
missing half of the reclaim sequence (agent-bridge-cold-resume Phase 3,
#6744): a caller stopping the interactive CLI via ``agent-worktrees
restart`` directly (not through this route) never gets that invalidation,
so the terminated CLI's stale row can keep the atomic ownership guard
(#2879) refusing a later ``resume``/``reclaim`` until its heartbeat
naturally lapses. ``agent_dispatch.bridge_reclaim`` (agent-dispatch's
automated take-over path) calls this verb, not ``agent-worktrees restart``
directly.
"""

from __future__ import annotations

import argparse
import sys


def _core():
    from . import __main__ as core
    return core


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "restart-worktree",
        help="Stop a worktree's interactive Copilot in place (keeps the "
        "worktree on disk) and expire its live-session registration -- "
        "the reclaim sequence's stop half",
    )
    p.add_argument("worktree_id", help="Worktree id whose interactive Copilot to stop")
    p.add_argument(
        "--force", action="store_true",
        help="Skip the graceful double-Ctrl-C quit; hard-kill the mux session immediately",
    )
    p.add_argument(
        "--expected-holder", default=None,
        help="Fence the server-side live-session invalidation to only this "
        "session id (a prior refusal's holder), so a genuinely different "
        "claimant that registers while this call is in flight is left "
        "untouched (#2906 race hardening)",
    )
    p.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="JSON output mode",
    )
    p.set_defaults(func=cmd_restart_worktree)


def cmd_restart_worktree(args: argparse.Namespace) -> None:
    core = _core()
    client = core._get_client()
    result = client.restart_worktree(
        args.worktree_id,
        force=bool(getattr(args, "force", False)),
        expected_holder=getattr(args, "expected_holder", None),
        request_timeout=core._startup_request_timeout(),
    )
    ok = bool(result.get("ok"))
    if getattr(args, "json", False):
        core._json_out(result)
        if not ok:
            sys.exit(1)
        return
    wt = result.get("worktree_id", args.worktree_id)
    if not ok:
        print(f"[FAIL] {wt}: failed to stop the interactive Copilot.", file=sys.stderr)
        sys.exit(1)
    if not result.get("had_session"):
        print(f"{wt}: no interactive Copilot running (nothing to stop).")
        return
    method = result.get("method")
    if method == "graceful":
        print(f"[OK] {wt}: Copilot quit gracefully (double Ctrl-C).")
    elif method == "hard":
        print(f"[OK] {wt}: Copilot hard-stopped (mux kill-session).")
    else:
        print(f"[FAIL] {wt}: failed to stop the interactive Copilot.", file=sys.stderr)
        sys.exit(1)
