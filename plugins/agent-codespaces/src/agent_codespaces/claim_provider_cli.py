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
import time

from .lease import (
    DeployHoldError,
    deploy_hold,
    get_lease,
    mark_deploy_hold_uncertain,
    verify_deploy_hold,
)
from .lease import release as release_lease
from .lifecycle import delete_codespace, get_codespace_status, get_codespace_status_with_account
from .sessions import sync_codespace_sessions

log = logging.getLogger("agent-codespaces")
_DELETE_TIMEOUT_SECONDS = 60.0
_DELETE_START_BUFFER_SECONDS = 5.0


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
    the callback contract. Confirms existence UP FRONT via
    ``lifecycle.get_codespace_status_with_account`` and fails closed on
    ANY lookup error (never falls back to ambient/re-derived credentials
    for what follows) or resolves immediately when the CodeSpace is
    confirmed already gone (``reclaimed: true``) -- rather than spending
    minutes attempting SSH session recovery on a resource that no longer
    exists (claim-provider-pattern effort review finding: "Fail closed on
    lookup errors and return immediately when absent"). Runs the same
    best-effort pre-delete Copilot-session recovery as ``agent-codespaces
    delete`` (``__main__._cmd_delete``) so a claim-reclaim invocation never
    destroys an unrecovered session -- unlike ``_cmd_delete``'s
    human-facing default (warn-and-continue), a FAILED recovery here
    blocks the delete entirely (``reclaimed: false``): this is an
    unattended/automated path with no operator present to notice the
    warning and intervene. A delete that itself fails because the
    CodeSpace turns out to already be gone (a race after the up-front
    check) still resolves as an idempotent reclaim, and, like the
    successful-delete path, releases any local lease on it (an
    already-gone resource must not leave a stale lease blocking
    allocation).

    Resolves the owning account ONCE up front, mints its token ONCE, and
    threads BOTH through session recovery and the delete itself -- never
    letting either helper independently re-derive (and possibly
    ambient-fallback for) credentials moments later. Fails closed if
    minting fails (review finding: "Preserve validated credentials during
    status and reclaim").
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
    # subsequent operation (session recovery, the delete itself) -- rather
    # than letting each one independently re-derive it via
    # account_for_codespace's own limited listing/ambient fallback, which
    # can disagree for a CodeSpace found only under a non-ambient or
    # beyond-first-page account (claim-provider-pattern effort review
    # finding: "Preserve the resolved account through CodeSpace
    # reclamation"). Unlike an earlier revision, a lookup FAILURE here is
    # NOT silently swallowed into "resolve independently later" -- an
    # authoritative status-lookup error must fail closed (never fall back
    # to ambient/re-derived credentials for a destructive operation), and a
    # CONFIRMED absence returns the idempotent result immediately rather
    # than spending minutes attempting SSH recovery on a resource that is
    # already gone (claim-provider-pattern effort review finding: "Fail
    # closed on lookup errors and return immediately when absent").
    try:
        exists, _state, resolved_account = get_codespace_status_with_account(args.name)
    except Exception as exc:
        print(json.dumps({"reclaimed": False, "detail": f"status lookup failed: {exc}"}))
        return 0
    if not exists:
        _release_lease_silently(args.name)
        print(json.dumps({"reclaimed": True, "detail": f"CodeSpace {args.name} already gone"}))
        return 0
    # sync_codespace_sessions/delete_codespace previously pinned to
    # resolved_account via gh_account.env_for_account -- a PERMISSIVE
    # helper that silently falls back to ambient credentials when it
    # cannot mint a token (its own documented contract for its other,
    # non-strict callers). Mint the token ONCE, right here, and thread the
    # EXACT token through both calls -- never letting either re-derive
    # (and possibly silently ambient-fallback for) credentials moments
    # later. Fail closed if minting itself fails (claim-provider-pattern
    # effort review finding: "Preserve validated credentials during
    # status and reclaim").
    resolved_token: str | None = None
    if resolved_account is not None:
        from . import gh_account
        resolved_token = gh_account.token_for_account(resolved_account)
        if not resolved_token:
            print(json.dumps({
                "reclaimed": False,
                "detail": (
                    f"could not mint an authenticated gh token for account "
                    f"{resolved_account}; refusing to reclaim under ambient fallback"
                ),
            }))
            return 0
    try:
        with deploy_hold(args.name, "claim-reclaim") as hold:
            # Re-check the lease INSIDE the fence too -- the earlier check,
            # above, ran before deploy_hold was even acquired, leaving a
            # window in which another effort could still acquire the lease
            # between that check and this hold actually taking effect.
            lease = get_lease(args.name)
            if lease:
                print(json.dumps({
                    "reclaimed": False,
                    "detail": f"CodeSpace is leased to {lease.effort}; release it first",
                }))
                return 0

            # session-rescue-parity Phase 1's lock-widening decision: hold the
            # SSH target lock (a distinct mechanism from deploy_hold's
            # provider-admission fence above) across sync+delete too, so a
            # concurrent capture cannot land between the sync completing and
            # the delete actually running.
            from ssh_manager import TargetBusyError, TargetLock

            target_lock = TargetLock(args.name, op="codespace-lifecycle")
            try:
                target_lock.acquire(force=False)
            except TargetBusyError as busy:
                print(json.dumps({
                    "reclaimed": False,
                    "detail": f"SSH target busy, deferring reclaim: {busy}",
                }))
                return 0
            try:
                try:
                    recovery = sync_codespace_sessions(
                        args.name, account=resolved_account, token=resolved_token,
                        lock=target_lock,
                    )
                except Exception as exc:
                    recovery = {"ok": False, "detail": str(exc)}
                if not recovery.get("ok"):
                    detail = recovery.get("detail", "")
                    print(json.dumps({
                        "reclaimed": False,
                        "detail": f"pre-delete session recovery failed: {detail}",
                    }))
                    return 0
                current_hold = verify_deploy_hold(args.name, hold.token)
                remaining = current_hold.expires_at - time.time()
                if remaining <= _DELETE_TIMEOUT_SECONDS + _DELETE_START_BUFFER_SECONDS:
                    mark_deploy_hold_uncertain(args.name, hold.token)
                    print(json.dumps({
                        "reclaimed": False,
                        "detail": (
                            "provider hold budget is nearly exhausted; "
                            "refusing to start deletion before the fence expires"
                        ),
                    }))
                    return 0
                try:
                    delete_codespace(
                        args.name,
                        force=True,
                        account=resolved_account,
                        token=resolved_token,
                    )
                except Exception as exc:
                    detail = str(exc)
                    if _codespace_looks_gone(detail):
                        _release_lease_silently(args.name)
                        print(json.dumps({
                            "reclaimed": True,
                            "detail": f"CodeSpace {args.name} already gone",
                        }))
                        return 0
                    mark_deploy_hold_uncertain(args.name, hold.token)
                    print(json.dumps({"reclaimed": False, "detail": detail}))
                    return 0
            finally:
                target_lock.release()
            _release_lease_silently(args.name)
    except DeployHoldError as exc:
        print(json.dumps({"reclaimed": False, "detail": str(exc)}))
        return 0
    except Exception as exc:
        print(json.dumps({
            "reclaimed": False,
            "detail": f"admission/hold check failed: {exc}",
        }))
        return 0
    print(json.dumps({"reclaimed": True, "detail": f"deleted CodeSpace {args.name}"}))
    return 0
