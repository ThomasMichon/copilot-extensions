"""Resume/version declarations retained from source 364d076b02bf.

These are the original declaration blocks from agent_bridge's
session_lifecycle_cli.py and service_start_cli.py, before exact CLI selection.
Handlers are supplied by the fixture; unsupported syntax must never reach one.
"""

from __future__ import annotations

import argparse


def _cmd_resume(args: argparse.Namespace) -> None:
    raise AssertionError("prior parser fixture must not dispatch")


_cmd_version = _cmd_resume


def register(sub: argparse._SubParsersAction) -> None:
    resume_p = sub.add_parser("resume", help="Resume a stopped session, or load/take-over a worktree by handle")
    from .hosting_admission_cli import add_argument as add_hosting_argument
    add_hosting_argument(resume_p)
    resume_p.add_argument("session_id", metavar="target", help="Session ID (owned ACP session) or worktree handle to load")
    resume_p.add_argument("--force", "--reclaim", dest="force", action="store_true", help="Break-glass take-over: adopt the worktree even if a live interactive CLI holds it (stop that CLI first)")
    resume_p.add_argument("--strict", action="store_true", help="Identity-preserving contract: refuse (409) rather than silently start a fresh replacement conversation when the worktree has no existing session or its resume fails")
    resume_p.add_argument("--cli", action="store_true", help="Resume the exact native head in a headed/muxed CLI instead of headless ACP")
    resume_p.set_defaults(func=_cmd_resume)

    ver_p = sub.add_parser("version", help="Print version")
    ver_p.set_defaults(func=_cmd_version)
