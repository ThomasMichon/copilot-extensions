"""``identities`` CLI command family: read-only worker-identity introspection.

``identities list|show`` report the named worker identities a declaration's
``worker_identity`` can select, resolved through the same tiers as
``worker_identities.load_worker_identity`` (marketplace overlay, repo-local,
legacy repo-local, packaged). Pure introspection: no writes, no network, no
coordinator. Output is JSON, matching the ``recipes`` family.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loop_commands import _resolve_cli_module


def _emit(value) -> int:
    return _resolve_cli_module()._emit(value)


def _repo_root(args: argparse.Namespace) -> Path | None:
    root = getattr(args, "repo_root", None)
    if not root:
        return None
    path = Path(root).expanduser()
    if not path.is_dir():
        raise ValueError(f"--repo-root {root!r} is not a directory")
    return path


def _cmd_identities_list(args: argparse.Namespace) -> int:
    from .worker_identities import list_worker_identities

    try:
        root = _repo_root(args)
    except ValueError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    return _emit([entry.to_dict() for entry in list_worker_identities(cwd=root)])


def _cmd_identities_show(args: argparse.Namespace) -> int:
    from .registrar import RegistrarError
    from .worker_identities import describe_worker_identity

    try:
        entry = describe_worker_identity(args.name, cwd=_repo_root(args))
    except (RegistrarError, ValueError) as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    return _emit(entry.to_dict(include_rules=True))


def register_identities_commands(sub) -> None:
    ip = sub.add_parser(
        "identities",
        help="worker identities -- list/show the named identities a declaration's worker_identity can select",
    )
    isub = ip.add_subparsers(dest="identities_command", required=True)

    repo_root_help = (
        "repository root to resolve repo-local identities against "
        "(default: the current directory, as at runtime)"
    )

    lp = isub.add_parser("list", help="list every effective identity with its source and description")
    lp.add_argument("--repo-root", metavar="PATH", help=repo_root_help)
    lp.set_defaults(func=_cmd_identities_list)

    sp = isub.add_parser("show", help="show one identity's provenance and full rules body")
    sp.add_argument("name", help="identity name (see 'identities list')")
    sp.add_argument("--repo-root", metavar="PATH", help=repo_root_help)
    sp.set_defaults(func=_cmd_identities_show)
