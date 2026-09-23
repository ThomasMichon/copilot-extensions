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

from .lifecycle import delete_codespace, list_codespaces


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
    404 / Not Found -- the resource is *already* reclaimed. Mirrors the
    equivalent heuristic agent-worktrees' own orphan-cleanup consumer used to
    apply locally before this claim-provider conversion -- now owned here,
    next to the resource it actually describes.
    """
    low = detail.lower()
    return "404" in low or "not found" in low or "could not resolve" in low


def cmd_claim_status(args: argparse.Namespace) -> int:
    """``claim-status <name>``: does CodeSpace NAME exist?

    Returns the small envelope ``agent_worktrees.claim_providers`` documents
    -- ``exists`` (required) plus ``state`` when known. Never raises: a
    listing failure degrades to ``exists: False`` with a ``detail`` (the
    registry's own caller treats this as a genuine callback failure only on
    non-JSON stdout/non-zero exit, not on this envelope)."""
    try:
        codespaces = list_codespaces()
    except Exception as exc:
        print(json.dumps({"exists": False, "detail": f"list failed: {exc}"}))
        return 0
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
    ``__main__`` to avoid a circular import."""
    if not args.apply:
        print(json.dumps({"reclaimed": True, "detail": f"would delete CodeSpace {args.name}"}))
        return 0
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
