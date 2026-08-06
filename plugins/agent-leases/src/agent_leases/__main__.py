"""Command-line interface for distributed Git-backed advisory leases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_settings
from .protocol import ProtocolError
from .store import GitError, GitLeaseStore, LeaseConflict, LeaseLost, LeaseSnapshot


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


def _print(snapshot: LeaseSnapshot | None, *, pretty: bool) -> None:
    if snapshot is None:
        data: object = {"state": "absent"}
    else:
        data = snapshot.to_dict()
    print(json.dumps(data, indent=2 if pretty else None, sort_keys=True))


def _run(args: argparse.Namespace) -> int:
    settings = load_settings(origin=args.origin, config_path=args.config)
    store = GitLeaseStore(settings)
    command = args.command
    if command in {"acquire", "borrow"}:
        snapshot = store.acquire(
            args.kind,
            args.resource,
            args.holder,
            ttl_seconds=args.ttl,
            context=_context(args.context),
            retries=args.retries,
        )
        _print(snapshot, pretty=args.pretty)
        return 0
    if command == "renew":
        context = _context(args.context) if args.context else None
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
        _print(
            store.release(args.kind, args.resource, args.token),
            pretty=args.pretty,
        )
        return 0
    if command in {"inspect", "status"}:
        _print(store.inspect(args.kind, args.resource), pretty=args.pretty)
        return 0
    if command == "list":
        data = [snapshot.to_dict() for snapshot in store.list(kind=args.kind)]
        print(json.dumps(data, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command {command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-leases", description=__doc__)
    parser.add_argument("--version", action="version", version=f"agent-leases {__version__}")
    parser.add_argument("--origin", help="override config key 'origin'")
    parser.add_argument("--config", type=Path, help="override the config.json path")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    subs = parser.add_subparsers(dest="command", required=True)

    def add_common_options(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--origin",
            default=argparse.SUPPRESS,
            help="override config key 'origin'",
        )
        command.add_argument(
            "--config",
            type=Path,
            default=argparse.SUPPRESS,
            help="override the config.json path",
        )
        command.add_argument(
            "--pretty",
            action="store_true",
            default=argparse.SUPPRESS,
            help="pretty-print JSON output",
        )

    def resource_command(name: str, help_text: str) -> argparse.ArgumentParser:
        command = subs.add_parser(name, help=help_text)
        command.add_argument("kind", help="resource kind, such as codespace or machine")
        command.add_argument("resource", help="canonical resource key")
        add_common_options(command)
        return command

    for name in ("acquire", "borrow"):
        command = resource_command(name, "atomically acquire a resource lease")
        command.add_argument("--holder", required=True, help="opaque holder/client identity")
        command.add_argument("--ttl", type=int, help="lease TTL in seconds")
        command.add_argument("--retries", type=int, help="bounded acquisition CAS retries")
        command.add_argument(
            "--context",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="bounded non-sensitive diagnostic context",
        )
    renew = resource_command("renew", "renew using the current fencing token")
    renew.add_argument("--token", required=True, help="current commit OID fencing token")
    renew.add_argument("--ttl", type=int, help="new TTL in seconds")
    renew.add_argument("--context", action="append", default=[], metavar="KEY=VALUE")
    release = resource_command("release", "append a release tombstone")
    release.add_argument("--token", required=True, help="current commit OID fencing token")
    resource_command("inspect", "inspect one lease")
    resource_command("status", "inspect one lease")
    listing = subs.add_parser("list", help="list leases in the configured namespace")
    add_common_options(listing)
    listing.add_argument("--kind", help="filter by resource kind")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except LeaseConflict as exc:
        print(f"lease conflict: {exc}", file=sys.stderr)
        return 3
    except LeaseLost as exc:
        print(f"lease lost: {exc}", file=sys.stderr)
        return 3
    except (ConfigError, ProtocolError) as exc:
        print(f"invalid lease state or configuration: {exc}", file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"git lease operation failed: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
