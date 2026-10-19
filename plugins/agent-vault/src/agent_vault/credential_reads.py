"""Cache-first credential reads and authoritative cache reconciliation."""

from __future__ import annotations

import contextlib
import os
import sys

from . import config


def cmd_get(args):
    from . import cli
    from .cache import get_cache

    entry = args.entry
    field = args.field or "password"
    try:
        cache_entry = config.normalize_entry(entry, config.resolve_context().group)
    except RuntimeError:
        print("Error: context_invalid; repair configured or pinned vault selection and retry",
              file=sys.stderr)
        return 1
    refresh = getattr(args, "refresh", False)
    cache_only = getattr(args, "cache_only", False)
    max_cache_age = getattr(args, "max_cache_age", None)
    cache = get_cache()
    pinned = config.PINNED_CONTEXT_ENV in os.environ

    if cache.enabled and not refresh:
        try:
            cached = cache.get(
                cache_entry,
                field,
                max_age_seconds=max_cache_age,
                legacy_entry=None if pinned else entry,
                **({"readonly": True} if pinned else {}),
            )
        except (OSError, RuntimeError, ValueError):
            if not pinned:
                raise
            print("Error: cache_read_failed; selected protected cache could not be read",
                  file=sys.stderr)
            return 1
        if cached is not None:
            print(cached)
            return 0
    if cache_only:
        if pinned:
            code = "cache_missing" if cache.enabled else "cache_unavailable"
            print(f"Error: {code}; selected entry is not available in the protected cache",
                  file=sys.stderr)
        else:
            print(f"Error: {entry} [{field}] not in cache", file=sys.stderr)
        return 1

    if not cli.ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1

    request = {
        "action": "get",
        "entry": entry,
        "field": field,
        "refresh": refresh or max_cache_age is not None,
    }
    if getattr(args, "prompt", False):
        request["allow_prompt"] = True
    authoritative = refresh or max_cache_age is not None
    read_lock = cache.replacement_lock() if authoritative else contextlib.nullcontext()
    succeeded = False
    reconciliation_failed = False
    with read_lock:
        resp = cli.send_command(request, timeout=None)
        if resp and resp.get("ok"):
            value = resp["value"]
            succeeded = True
            if cache.enabled:
                generation = resp.get("generation")
                if authoritative:
                    reconciliation_failed = not cache.reconcile_authoritative(
                        cache_entry,
                        field,
                        value,
                        int(generation) if generation is not None else None,
                    )
                elif generation is None:
                    cache.put(cache_entry, field, value)
                else:
                    cache.put(cache_entry, field, value, int(generation))
    if reconciliation_failed:
        print(
            "Error: credential was read, but the persistent cache could not be "
            "reconciled",
            file=sys.stderr,
        )
        return 2
    if succeeded:
        print(value)
        return 0

    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1
