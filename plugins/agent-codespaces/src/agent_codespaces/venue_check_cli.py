"""CLI glue for ``agent-codespaces check <name>`` / ``doctor <name> [--fix]``.

Split out of ``__main__.py`` (module-size guard) -- owns only connecting to
the target CodeSpace and formatting output; the actual probe/remediation
logic lives in :mod:`agent_codespaces.venue_check`, which is fully
transport-agnostic (fakeable in tests without a real SSH connection).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import venue_check
from .codespace_config import CodespaceSource


async def _connect_readonly(name: str):
    """Establish (or reuse) a plain SSH connection for a diagnostic probe.

    No relay, no port forwards, no exclusive worktree claim: `check`/`doctor`
    are read-mostly diagnostics safe to run alongside another active session
    on the same CodeSpace. Returns the live ``ConnectionManager`` so the
    caller can also disconnect when done.
    """
    from ssh_manager import ConnectionManager

    from .lifecycle import account_for_codespace

    source = CodespaceSource(name, account=account_for_codespace(name))
    manager = ConnectionManager()
    await manager.ensure_connected(name, source, [])
    return manager


async def cmd_check(args: argparse.Namespace) -> int:
    """``agent-codespaces check <name>`` -- read-only venue readiness probe."""
    manager = await _connect_readonly(args.name)
    try:
        readiness = await venue_check.check_remote_venue(
            manager.exec_command, args.name, timeout=args.timeout,
        )
    finally:
        await manager.disconnect(args.name)

    if args.json_output:
        print(json.dumps(readiness.to_dict(), indent=2, sort_keys=True))
    else:
        print(venue_check.format_report(readiness))
    return 0 if readiness.ready else 1


async def cmd_doctor_venue(args: argparse.Namespace) -> int:
    """``agent-codespaces doctor <name> [--fix]`` -- probe, optionally remediate."""
    manager = await _connect_readonly(args.name)
    try:
        readiness = await venue_check.check_remote_venue(
            manager.exec_command, args.name, timeout=args.timeout,
        )
        remediation = None
        if args.fix and readiness.gaps:
            remediation = await venue_check.remediate_remote_venue(
                manager.exec_command, args.name, readiness, timeout=args.timeout,
            )
            # Re-probe so the report reflects what remediation actually
            # achieved, not the pre-fix snapshot.
            readiness = await venue_check.check_remote_venue(
                manager.exec_command, args.name, timeout=args.timeout,
            )
    finally:
        await manager.disconnect(args.name)

    if args.json_output:
        payload = readiness.to_dict()
        if remediation is not None:
            payload["remediation"] = remediation.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(venue_check.format_report(readiness, remediation=remediation))
        if readiness.gaps and not args.fix:
            print(
                "\nRun with --fix to attempt the safely-idempotent "
                "remediations this command knows (tmux install, "
                "agent-worktrees provision/refresh).",
                file=sys.stderr,
            )
    return 0 if readiness.ready else 1
