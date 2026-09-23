"""Claim-provider callback commands (claim-provider-pattern effort).

``claim-status``/``claim-reclaim`` for the ``codespace:`` namespace
agent-worktrees' claim-provider registry (``agent_worktrees.claim_providers``)
resolves -- never ambient ``PATH``, always this plugin's own payload-local
``bin/agent-codespaces`` binstub. Not human-facing; split out of
``__main__.py`` to stay under its grandfathered module-size ceiling.
"""
from __future__ import annotations

import argparse
import functools
import json
import sys

from .lifecycle import delete_codespace, list_codespaces
from .sessions import sync_codespace_sessions


def add_claim_provider_parsers(sub, *, release_lease_quietly) -> None:
    """Register the ``claim-status``/``claim-reclaim`` subcommands.

    Not human-facing: the callback contract agent-worktrees' claim-provider
    registry invokes for the ``codespace:`` namespace (never ambient
    ``PATH`` -- resolved to this plugin's own payload-local binstub). Wires
    each subparser's ``func`` default directly (rather than a separate
    dispatch branch in ``__main__``) to stay under its size ceiling."""
    claim_status_parser = sub.add_parser(
        "claim-status",
        help="claim-provider callback: does CodeSpace NAME exist "
        "(for agent-worktrees' 'codespace:' claim provider registry entry)",
    )
    claim_status_parser.add_argument("name", help="CodeSpace name")
    claim_status_parser.set_defaults(func=cmd_claim_status)

    claim_reclaim_parser = sub.add_parser(
        "claim-reclaim",
        help="claim-provider callback: reclaim (delete) CodeSpace NAME -- "
        "dry-run unless --apply (for agent-worktrees' 'codespace:' claim "
        "provider registry entry)",
    )
    claim_reclaim_parser.add_argument("name", help="CodeSpace name")
    claim_reclaim_parser.add_argument(
        "--apply", action="store_true", help="Actually delete (default: dry-run preview)",
    )
    claim_reclaim_parser.set_defaults(
        func=functools.partial(cmd_claim_reclaim, release_lease_quietly=release_lease_quietly))


def _codespace_looks_gone(detail: str) -> bool:
    """Heuristic: does a failed delete mean the CodeSpace is already gone?

    ``gh codespace delete`` on a non-existent box exits non-zero with an HTTP
    404 / Not Found -- the resource is *already* reclaimed. Deliberately
    narrow: an ambiguous transient failure (e.g. ``could not resolve host``
    during a network/DNS outage) must NOT match here, or a live obligation
    could be discarded as "already reclaimed" during an outage. Mirrors the
    equivalent heuristic agent-worktrees' own orphan-cleanup consumer used to
    apply locally before this claim-provider conversion -- now owned here,
    next to the resource it actually describes.
    """
    low = detail.lower()
    return "404" in low or "not found" in low


def cmd_claim_status(args: argparse.Namespace) -> int:
    """``claim-status <name>``: does CodeSpace NAME exist?

    Returns the small envelope ``agent_worktrees.claim_providers`` documents
    -- ``exists`` (required) plus ``state`` when known. A listing failure
    (gh/network/auth trouble) is a genuine callback failure, NOT a confirmed
    absence -- exits non-zero with a stderr message so the registry's own
    caller degrades to ``{"available": false, ...}`` instead of a false
    ``exists: false`` that could make a live claim look reclaimable."""
    try:
        codespaces = list_codespaces()
    except Exception as exc:
        print(f"codespace listing failed: {exc}", file=sys.stderr)
        return 1
    for cs in codespaces:
        if cs.name == args.name:
            print(json.dumps({"exists": True, "state": cs.state}))
            return 0
    print(json.dumps({"exists": False}))
    return 0


def cmd_claim_reclaim(args: argparse.Namespace, *, release_lease_quietly) -> int:
    """``claim-reclaim <name> [--apply]``: reclaim (delete) CodeSpace NAME.

    Without ``--apply`` this is a dry-run preview only (never deletes), per
    the callback contract. Idempotent: a delete failing because the
    CodeSpace is already gone (404/not-found) still reports
    ``reclaimed: true``. ``release_lease_quietly`` is injected from
    ``__main__`` to avoid a circular import. Runs the same best-effort
    pre-delete Copilot-session recovery as ``agent-codespaces delete``
    (``__main__._cmd_delete``) so a claim-reclaim invocation never destroys
    an unrecovered session."""
    if not args.apply:
        print(json.dumps({"reclaimed": True, "detail": f"would delete CodeSpace {args.name}"}))
        return 0
    try:
        sync_codespace_sessions(args.name)
    except Exception as exc:
        print(f"pre-delete session recovery failed (continuing): {exc}", file=sys.stderr)
    try:
        delete_codespace(args.name, force=True)
    except Exception as exc:
        detail = str(exc)
        if _codespace_looks_gone(detail):
            print(json.dumps({"reclaimed": True, "detail": f"CodeSpace {args.name} already gone"}))
            return 0
        print(json.dumps({"reclaimed": False, "detail": detail}))
        return 0
    release_lease_quietly(args.name)
    print(json.dumps({"reclaimed": True, "detail": f"deleted CodeSpace {args.name}"}))
    return 0
