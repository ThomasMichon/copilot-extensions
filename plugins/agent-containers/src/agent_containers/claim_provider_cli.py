"""Claim-provider callback commands (claim-provider-pattern effort).

``claim-status``/``claim-reclaim`` for the ``container:`` namespace
agent-worktrees' claim-provider registry (``agent_worktrees.claim_providers``)
resolves -- never ambient ``PATH``, always this plugin's own payload-local
``bin/agent-containers`` binstub. Not human-facing; split out of
``__main__.py`` to stay under its grandfathered module-size ceiling.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import lifecycle
from .lease import active_session_admissions, get_deploy_hold, get_lease
from .lease import release as release_lease

log = logging.getLogger("agent-containers")


def add_claim_provider_parsers(sub) -> None:
    """Register the ``claim-status``/``claim-reclaim`` subcommands.

    Not human-facing: the callback contract agent-worktrees' claim-provider
    registry invokes for the ``container:`` namespace (never ambient
    ``PATH`` -- resolved to this plugin's own payload-local binstub)."""
    claim_status_p = sub.add_parser(
        "claim-status",
        help="claim-provider callback: does container NAME exist "
        "(for agent-worktrees' 'container:' claim provider registry entry)",
    )
    claim_status_p.add_argument("name", help="Container name")
    claim_status_p.set_defaults(func=cmd_claim_status)

    claim_reclaim_p = sub.add_parser(
        "claim-reclaim",
        help="claim-provider callback: reclaim (remove) container NAME -- "
        "dry-run unless --apply (for agent-worktrees' 'container:' claim "
        "provider registry entry)",
    )
    claim_reclaim_p.add_argument("name", help="Container name")
    claim_reclaim_p.add_argument(
        "--apply", action="store_true", help="Actually remove (default: dry-run preview)",
    )
    claim_reclaim_p.set_defaults(func=cmd_claim_reclaim)


def _container_looks_gone(detail: str) -> bool:
    """Heuristic: does a failed remove mean the container is already gone?

    ``docker rm`` on a non-existent container exits non-zero with a "No such
    container" message -- the resource is *already* reclaimed."""
    return "no such container" in detail.lower()


def _release_lease_silently(name: str) -> None:
    """Release a container lease. Best-effort: never raises (a stale lease
    after a terminal reclaim would otherwise keep blocking allocation for
    the lease TTL -- see ``lease.release``'s own ``ProviderAdmissionError``,
    which this simply logs rather than propagates)."""
    try:
        if release_lease(name):
            log.info("Released lease on %s", name)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("lease release for %s failed: %s", name, exc)


def cmd_claim_status(args: argparse.Namespace) -> int:
    """``claim-status <name>``: does container NAME exist?

    Returns the small envelope ``agent_worktrees.claim_providers`` documents
    -- ``exists`` (required) plus ``state`` when known. ``lifecycle.
    inspect_state`` collapses every failure (missing container, unreachable
    Docker daemon, permission error) to the same ``None``, which would make
    an unrelated backend outage look like a confirmed absence -- so this
    calls ``docker inspect`` directly and inspects ``stderr`` to distinguish
    a genuine "no such container" from a real backend error, which exits
    non-zero (a callback failure, degrading to ``{"available": false, ...}``
    at the registry) instead of a false ``exists: false``."""
    try:
        proc = lifecycle._docker(["inspect", "-f", "{{.State.Status}}", args.name])
    except Exception as exc:
        print(f"docker inspect failed: {exc}", file=sys.stderr)
        return 1
    if proc.returncode != 0:
        stderr_low = (proc.stderr or "").lower()
        if "no such container" in stderr_low or "no such object" in stderr_low:
            print(json.dumps({"exists": False}))
            return 0
        print(f"docker inspect {args.name} failed: "
              f"{(proc.stderr or proc.stdout).strip()}", file=sys.stderr)
        return 1
    print(json.dumps({"exists": True, "state": proc.stdout.strip().lower() or None}))
    return 0


def cmd_claim_reclaim(args: argparse.Namespace) -> int:
    """``claim-reclaim <name> [--apply]``: reclaim (remove) container NAME.

    Without ``--apply`` this is a dry-run preview only (never removes), per
    the callback contract. Idempotent: a remove failing because the
    container is already gone ("no such container") still reports
    ``reclaimed: true`` -- and, like the successful-remove path, releases
    any local lease on it (an already-gone resource must not leave a stale
    lease blocking allocation for the lease TTL). Refuses (``reclaimed:
    false``) an actively leased container -- the same guard
    ``lifecycle.cmd_remove`` applies -- so a stale worktree claim can never
    destroy a container another effort currently holds."""
    if not args.apply:
        print(json.dumps({"reclaimed": True, "detail": f"would remove container {args.name}"}))
        return 0
    lease = get_lease(args.name)
    if lease:
        print(json.dumps({
            "reclaimed": False,
            "detail": f"container is leased to {lease.effort}; release it first",
        }))
        return 0
    # A restricted `exec --stdio` session (or a live provider-lifecycle
    # deploy hold) can hold a container without an advisory lease at all --
    # check both, fail closed (refuse) on any error reading either, and
    # never proceed to the destructive removal below with either present.
    try:
        admissions = active_session_admissions(args.name)
        hold = get_deploy_hold(args.name)
    except Exception as exc:
        print(json.dumps({
            "reclaimed": False,
            "detail": f"admission/hold check failed: {exc}",
        }))
        return 0
    if admissions or hold:
        print(json.dumps({
            "reclaimed": False,
            "detail": "container has an active session admission or provider "
            "lifecycle hold; refusing to remove",
        }))
        return 0
    try:
        lifecycle.remove_container(args.name, force=True)
    except RuntimeError as exc:
        detail = str(exc)
        if _container_looks_gone(detail):
            _release_lease_silently(args.name)
            print(json.dumps({"reclaimed": True, "detail": f"container {args.name} already gone"}))
            return 0
        print(json.dumps({"reclaimed": False, "detail": detail}))
        return 0
    _release_lease_silently(args.name)
    print(json.dumps({"reclaimed": True, "detail": f"removed container {args.name}"}))
    return 0
