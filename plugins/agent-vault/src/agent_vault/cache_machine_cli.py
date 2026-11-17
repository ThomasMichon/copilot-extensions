"""Explicit preparation and credential-free verification of protected caches."""

from __future__ import annotations

import json
import sys
from typing import Any

from . import config
from .cache_dpapi import CacheProtectionError
from .cache_targets import command_entries
from .credential_fields import attribute_name
from .credential_reads import legacy_cache_entry


def cmd_cache_machine_prepare(args: Any) -> int:
    """Create only the additive machine copy after explicit risk acceptance."""
    from .cache import get_cache
    from .cache_machine import prepare

    if not getattr(args, "accept_machine_scope", False):
        print("Error: --accept-machine-scope is required; administrators can decrypt this copy",
              file=sys.stderr)
        return 2
    try:
        result = prepare(get_cache())
    except (OSError, RuntimeError, ValueError, ImportError):
        print("Error: machine cache preparation failed; primary retained", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


def register_prepare(subparsers) -> None:
    parser = subparsers.add_parser(
        "cache-machine-prepare",
        help="Opt in to a redundant Windows machine-DPAPI copy without changing the user cache",
    )
    parser.add_argument("--accept-machine-scope", action="store_true",
                        help="Accept administrator/SYSTEM decryptability")
    parser.set_defaults(func=cmd_cache_machine_prepare)


def cmd_cache_verify(args: Any) -> int:
    """Verify selected cache entries without exposing values or starting a service."""
    from .cache import get_cache
    pairs = command_entries(args)
    if pairs is None:
        return 1
    if not pairs:
        print("No entries to verify (no declared targets, --entry, --manifest, or extension).",
              file=sys.stderr)
        return 1
    cache = get_cache()
    present = 0
    missing: list[str] = []
    try:
        for entry, field in pairs:
            cache_entry = config.normalize_entry(entry, config.resolve_context().group)
            if cache.get(
                cache_entry, field, readonly=True, legacy_field=attribute_name(field),
                legacy_entry=legacy_cache_entry(entry, cache_entry),
                **({"machine_only": True} if getattr(args, "machine_fallback", False) else {}),
            ) is not None:
                present += 1
            else:
                missing.append(f"{entry} [{field}]")
    except (OSError, CacheProtectionError, ValueError, ImportError):
        print("Error: protected cache verification unavailable", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"present": present, "total": len(pairs), "missing": missing}, indent=2))
    else:
        print(f"Cached: {present}/{len(pairs)}")
        for item in missing:
            print(f"  missing: {item}", file=sys.stderr)
    return 2 if missing else 0
