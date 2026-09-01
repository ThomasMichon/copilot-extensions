"""``agent-worktrees lease`` -- CLI for the Git-ref resource lease store.

Atomic, cross-machine, same-harness advisory leases on scarce shared resources
(CodeSpaces, cross-repo worktrees, containers, bridges), backed by
compare-and-swap on hidden refs in an explicitly selected private state repo
(see ``lease_store.py`` / ``lease_config.py``). The verb surface mirrors the
upstream ``agent-leases`` CLI (ThomasMichon/copilot-extensions#180) so the
loosely-coupled resource plugins can shell into it:

    agent-worktrees lease acquire <kind> <key> --holder <ref> [--ttl N]
    agent-worktrees lease renew   <kind> <key> --token <oid> [--ttl N]
    agent-worktrees lease release <kind> <key> --token <oid>
    agent-worktrees lease inspect <kind> <key> [--pretty]
    agent-worktrees lease list [--kind <kind>] [--pretty]

``borrow`` aliases ``acquire``; ``status`` aliases ``inspect``. All output is
JSON. The fencing token is the returned commit ``token``; persist it and stop
using the resource if renewal fails or ``safe_deadline`` passes.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import obligations
from .lease_config import (
    ConfigError,
    CoordinationReadinessError,
    load_acquisition_lease_settings,
    load_lease_settings,
)
from .lease_protocol import ProtocolError
from .lease_store import (
    GitError,
    GitLeaseStore,
    LeaseConflict,
    LeaseLost,
    LeaseSnapshot,
)


def _context(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ProtocolError("--context values must use KEY=VALUE")
        key, item = value.split("=", 1)
        if key in result:
            raise ProtocolError(f"duplicate context key: {key}")
        result[key] = item
    return result


def _with_disposition(context: dict[str, str], disposition: str | None) -> dict[str, str]:
    """Overlay an explicit ``--disposition`` onto the context map, if given.

    The obligation disposition (resource-obligation-settlement) rides the lease's
    diagnostic ``context`` under the ``disposition`` key -- so ``--disposition``
    is sugar for ``--context disposition=<value>``. When ``--disposition`` is
    omitted, the context (which may carry its own ``disposition=`` KEY=VALUE) is
    returned unchanged.
    """
    if not disposition:
        return context
    return obligations.with_disposition(context, disposition)


def _print(snapshot: LeaseSnapshot | None, *, pretty: bool) -> None:
    data: object = {"state": "absent"} if snapshot is None else snapshot.to_dict()
    print(json.dumps(data, indent=2 if pretty else None, sort_keys=True))


def _run(args: argparse.Namespace) -> int:
    command = args.command
    settings = (
        load_acquisition_lease_settings(origin=args.origin)
        if command in {"acquire", "borrow"}
        else load_lease_settings(origin=args.origin)
    )
    store = GitLeaseStore(settings)
    if command in {"acquire", "borrow"}:
        context = _with_disposition(_context(args.context), getattr(args, "disposition", None))
        snapshot = store.acquire(
            args.kind,
            args.resource,
            args.holder,
            ttl_seconds=args.ttl,
            context=context,
            retries=args.retries,
        )
        _print(snapshot, pretty=args.pretty)
        return 0
    if command == "renew":
        disposition = getattr(args, "disposition", None)
        # Preserve the store's existing context when neither --context nor
        # --disposition is given (context=None tells renew to keep it); otherwise
        # start from --context and overlay --disposition.
        if args.context or disposition:
            context = _with_disposition(_context(args.context), disposition)
        else:
            context = None
        snapshot = store.renew(
            args.kind,
            args.resource,
            args.token,
            ttl_seconds=args.ttl,
            context=context,
        )
        _print(snapshot, pretty=args.pretty)
        return 0
    if command == "release":
        _print(store.release(args.kind, args.resource, args.token), pretty=args.pretty)
        return 0
    if command in {"inspect", "status"}:
        _print(store.inspect(args.kind, args.resource), pretty=args.pretty)
        return 0
    if command == "list":
        data = [snapshot.to_dict() for snapshot in store.list(kind=args.kind)]
        print(json.dumps(data, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled lease command {command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees lease", description=__doc__
    )
    parser.add_argument(
        "--origin",
        help="explicit private lease-store origin URL",
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    subs = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--origin", default=argparse.SUPPRESS,
            help="explicit private lease-store origin URL",
        )
        command.add_argument(
            "--pretty", action="store_true", default=argparse.SUPPRESS,
            help="pretty-print JSON",
        )

    def resource_command(name: str, help_text: str) -> argparse.ArgumentParser:
        command = subs.add_parser(name, help=help_text)
        command.add_argument("kind", help="resource kind, e.g. codespace / worktree")
        command.add_argument("resource", help="canonical resource key")
        common(command)
        return command

    for name in ("acquire", "borrow"):
        command = resource_command(name, "atomically acquire a resource lease")
        command.add_argument(
            "--holder", required=True,
            help="holder identity (a machine/project/worktree_id[#session] ClaimRef)",
        )
        command.add_argument("--ttl", type=int, help="lease TTL in seconds")
        command.add_argument("--retries", type=int, help="bounded acquisition CAS retries")
        command.add_argument(
            "--context", action="append", default=[], metavar="KEY=VALUE",
            help="bounded non-sensitive diagnostic context",
        )
        command.add_argument(
            "--disposition", choices=list(obligations.DISPOSITIONS),
            help="obligation disposition (rides context: active|at-rest|released)",
        )
    renew = resource_command("renew", "renew using the current fencing token")
    renew.add_argument("--token", required=True, help="current commit OID fencing token")
    renew.add_argument("--ttl", type=int, help="new TTL in seconds")
    renew.add_argument("--context", action="append", default=[], metavar="KEY=VALUE")
    renew.add_argument(
        "--disposition", choices=list(obligations.DISPOSITIONS),
        help="settle/advance the obligation disposition (active|at-rest|released)",
    )
    release = resource_command("release", "append a release tombstone")
    release.add_argument("--token", required=True, help="current commit OID fencing token")
    resource_command("inspect", "inspect one lease")
    resource_command("status", "inspect one lease")
    listing = subs.add_parser("list", help="list leases in the namespace")
    common(listing)
    listing.add_argument("--kind", help="filter by resource kind")
    return parser


def run_lease(argv: list[str]) -> int:
    """Entry point for the ``agent-worktrees lease`` verb (manual dispatch)."""
    if argv and argv[0] in ("-h", "--help", "help"):
        _parser().print_help()
        return 0
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except LeaseConflict as exc:
        print(f"lease conflict: {exc}", file=sys.stderr)
        return 3
    except LeaseLost as exc:
        print(f"lease lost: {exc}", file=sys.stderr)
        return 3
    except CoordinationReadinessError as exc:
        print(
            json.dumps({
                "error": str(exc),
                "code": exc.readiness.code,
                "coordination_readiness": exc.readiness.as_dict(),
            }),
            file=sys.stderr,
        )
        return 5
    except (ConfigError, ProtocolError) as exc:
        print(f"invalid lease state or configuration: {exc}", file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"git lease operation failed: {exc}", file=sys.stderr)
        return 4
