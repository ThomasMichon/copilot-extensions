"""Claim-provider callback commands (claim-provider-pattern effort).

``claim-status``/``claim-reclaim`` for the ``codespace:`` namespace
agent-worktrees' claim-provider registry (``agent_worktrees.claim_providers``)
resolves -- never ambient ``PATH``, always this plugin's own payload-local
``bin/agent-codespaces`` binstub. Not human-facing; split out of
``__main__.py`` to stay under its grandfathered module-size ceiling.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .lease import get_lease
from .lease import release as release_lease
from .lifecycle import delete_codespace, get_codespace_status, get_codespace_status_with_account
from .sessions import sync_codespace_sessions

log = logging.getLogger("agent-codespaces")


def add_claim_provider_parsers(sub) -> None:
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
    claim_reclaim_parser.set_defaults(func=cmd_claim_reclaim)


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


def _release_lease_silently(name: str) -> None:
    """Release a CodeSpace lease WITHOUT printing (unlike ``__main__.
    _release_lease_quietly``, which prints ``[OK] Released lease ...`` to
    STDOUT -- fine for a human-facing command, but fatal here: it would
    corrupt this callback's JSON-only stdout contract, making the
    registry's own ``json.loads`` fail and report the provider
    unavailable). Best-effort: never raises."""
    try:
        if release_lease(name):
            log.info("Released lease on %s", name)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("lease release for %s failed: %s", name, exc)


def cmd_claim_status(args: argparse.Namespace) -> int:
    """``claim-status <name>``: does CodeSpace NAME exist?

    Returns the small envelope ``agent_worktrees.claim_providers`` documents
    -- ``exists`` (required) plus ``state`` when known. Uses
    ``lifecycle.get_codespace_status`` -- a strict, targeted single-CodeSpace
    lookup -- rather than the paginated, best-effort ``list_codespaces()``
    (``--limit 50``, drops rows on a failed account), so a live CodeSpace
    outside the first page or in a failed account is never misreported as
    absent. Any backend failure (gh/network/auth trouble, or an ambiguous
    listing) is a genuine callback failure, NOT a confirmed absence -- exits
    non-zero with a stderr message so the registry's own caller degrades to
    ``{"available": false, ...}`` instead of a false ``exists: false`` that
    could make a live claim look reclaimable."""
    try:
        exists, state = get_codespace_status(args.name)
    except Exception as exc:
        print(f"codespace status lookup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"exists": exists, "state": state} if exists else {"exists": False}))
    return 0


def cmd_claim_reclaim(args: argparse.Namespace) -> int:
    """``claim-reclaim <name> [--apply]``: reclaim (delete) CodeSpace NAME.

    Without ``--apply`` this is a dry-run preview only (never deletes), per
    the callback contract. Idempotent: a delete failing because the
    CodeSpace is already gone (404/not-found) still reports
    ``reclaimed: true`` -- and, like the successful-delete path, releases
    any local lease on it (an already-gone resource must not leave a stale
    lease blocking allocation). Runs the same best-effort pre-delete
    Copilot-session recovery as ``agent-codespaces delete``
    (``__main__._cmd_delete``) so a claim-reclaim invocation never destroys
    an unrecovered session -- unlike ``_cmd_delete``'s human-facing default
    (warn-and-continue), a FAILED recovery here blocks the delete entirely
    (``reclaimed: false``): this is an unattended/automated path with no
    operator present to notice the warning and intervene -- UNLESS recovery
    failed only because the CodeSpace is already gone (nothing to connect
    to, so nothing to recover), confirmed via a real existence check, which
    still resolves as an idempotent reclaim.

    ``sync_codespace_sessions`` uses its own default timeout (300s, matching
    ``cleanup._run_codespaces``'s own default and
    ``claim_providers.resolve_claim_reclaim``'s reclaim-specific callback
    timeout) -- a real reclaim (session recovery + delete, both over the
    network) needs materially more budget than a quick status check.

    Resolves the owning account ONCE up front (via
    ``lifecycle.get_codespace_status_with_account``) and threads it through
    session recovery, the existence recheck, and the delete itself, rather
    than letting each independently re-derive it.
    """
    if not args.apply:
        print(json.dumps({"reclaimed": True, "detail": f"would delete CodeSpace {args.name}"}))
        return 0
    lease = get_lease(args.name)
    if lease:
        print(json.dumps({
            "reclaimed": False,
            "detail": f"CodeSpace is leased to {lease.effort}; release it first",
        }))
        return 0
    # Resolve the owning account ONCE, up front, and thread it through every
    # subsequent operation (session recovery, the existence recheck, the
    # delete itself) -- rather than letting each one independently re-derive
    # it via account_for_codespace's own limited listing/ambient fallback,
    # which can disagree for a CodeSpace found only under a non-ambient or
    # beyond-first-page account (claim-provider-pattern effort review
    # finding: "Preserve the resolved account through CodeSpace
    # reclamation"). Best-effort: an ambiguous/failed resolution here falls
    # back to each operation resolving independently, same as before.
    try:
        _exists, _state, resolved_account = get_codespace_status_with_account(args.name)
    except Exception:
        resolved_account = None
    try:
        recovery = sync_codespace_sessions(args.name, account=resolved_account)
    except Exception as exc:
        recovery = {"ok": False, "detail": str(exc)}
    if not recovery.get("ok"):
        # Recovery failing because the CodeSpace is ALREADY gone (nothing to
        # connect to -> nothing to recover) must still resolve as an
        # idempotent reclaim, not a refusal -- confirm via a real existence
        # check rather than guessing from the recovery failure text alone.
        try:
            exists, _state = get_codespace_status(args.name, account=resolved_account)
        except Exception:
            exists = True  # ambiguous -- treat as present, refuse below
        if not exists:
            _release_lease_silently(args.name)
            print(json.dumps({"reclaimed": True, "detail": f"CodeSpace {args.name} already gone"}))
            return 0
        print(json.dumps({
            "reclaimed": False,
            "detail": f"pre-delete session recovery failed: {recovery.get('detail', '')}",
        }))
        return 0
    # Narrow (not eliminate -- this lease store has no atomic hold/fence
    # primitive to extend across a long external operation) the window
    # between the initial lease check above and the destructive delete
    # below: session recovery can itself run for minutes, during which
    # another effort could legitimately acquire the CodeSpace. Re-verify
    # immediately before the point of no return rather than trusting a
    # check made minutes earlier.
    lease = get_lease(args.name)
    if lease:
        print(json.dumps({
            "reclaimed": False,
            "detail": f"CodeSpace is leased to {lease.effort}; release it first",
        }))
        return 0
    try:
        delete_codespace(args.name, force=True, account=resolved_account)
    except Exception as exc:
        detail = str(exc)
        if _codespace_looks_gone(detail):
            _release_lease_silently(args.name)
            print(json.dumps({"reclaimed": True, "detail": f"CodeSpace {args.name} already gone"}))
            return 0
        print(json.dumps({"reclaimed": False, "detail": detail}))
        return 0
    _release_lease_silently(args.name)
    print(json.dumps({"reclaimed": True, "detail": f"deleted CodeSpace {args.name}"}))
    return 0
