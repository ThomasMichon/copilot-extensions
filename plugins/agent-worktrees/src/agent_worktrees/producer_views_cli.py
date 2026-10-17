"""Read-only ownership CLI, deliberately separate from mutation/cleanup paths."""

from __future__ import annotations

import argparse
import json

from . import output, resource_producer as producer
from .producer_view_scan import scan_ownership
from .producer_views import qualified_owner


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate identity JSON key")
        result[key] = value
    return result


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ownership-only selectors to the existing claims parser."""
    parser.add_argument("--namespace", dest="claim_namespace", help="with ownership: exact producer namespace")
    parser.add_argument("--kind", dest="claim_kind", help="with ownership: exact resource kind")
    parser.add_argument("--identity-json", help="with ownership: exact complete opaque bound identity JSON object")


def cmd_ownership(args: argparse.Namespace, target: list[str]) -> int:
    """Print a conservative snapshot; uncertainty always exits nonzero."""
    json_mode = getattr(args, "json", False)
    try:
        if target:
            raise ValueError("claims ownership takes no positional selectors")
        owner = getattr(args, "claim_owner_ref", None)
        if owner is not None and not qualified_owner(owner):
            raise ValueError("--owner-ref must be an exact qualified worktree reference without a session")
        identity = None
        raw = getattr(args, "identity_json", None)
        if raw is not None:
            if len(raw.encode("utf-8")) > producer.MAX_INPUT_BYTES:
                raise ValueError("identity JSON exceeds size limit")
            identity = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(identity, dict):
                raise ValueError("--identity-json must be a JSON object")
            producer.canonical_json(identity)
        result = scan_ownership(
            owner=owner, kind=getattr(args, "claim_kind", None),
            namespace=getattr(args, "claim_namespace", None), identity=identity,
        )
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if json_mode:
            return output._json_error(f"claims ownership: {exc}", 2)
        output.err(f"claims ownership: {exc}")
        return 2
    if json_mode:
        output._json_output(result)
    else:
        print(f"Local ledger snapshot: {result['resolution']} (not absence or cleanup authority)")
        for row in result["entries"]:
            print(f"{row['record_owner']}  {row['kind']}  {row['phase']}  {row['resolution']}  {row['ref']}")
        for finding in result["issues"]:
            output.err(f"{finding['code']}: {finding['subject']}: {finding['message']}")
    return 0 if result["complete"] else 1
