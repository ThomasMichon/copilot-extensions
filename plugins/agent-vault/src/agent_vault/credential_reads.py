"""Cache-first credential reads and authoritative cache reconciliation."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import os
import subprocess
import sys

from . import config
from .cache_dpapi import CacheDecryptionUnavailable
from .credential_fields import attribute_name, canonical_field

CACHE_DECRYPTION_WARNING = (
    "cache_decryption_unavailable; protected cache retained; service-only read"
)


@dataclass(frozen=True)
class CredentialRead:
    code: int
    value: str | None = None
    error: str | None = None
    source: str = "service"
    warning: str | None = None


def read_credential(args) -> CredentialRead:
    from . import cli
    from .cache import get_cache

    entry = args.entry
    field = canonical_field(args.field or "password")
    try:
        cache_entry = config.normalize_entry(entry, config.resolve_context().group)
    except (RuntimeError, ValueError, OSError) as exc:
        if not isinstance(exc, RuntimeError) and not getattr(args, "strict_cache", False):
            raise
        return CredentialRead(
            1, error="context_invalid; repair configured or pinned vault selection and retry",
            source="none",
        )
    refresh = getattr(args, "refresh", False)
    cache_only = getattr(args, "cache_only", False)
    max_cache_age = getattr(args, "max_cache_age", None)
    cache = get_cache()
    pinned = config.PINNED_CONTEXT_ENV in os.environ
    strict_cache = getattr(args, "strict_cache", False)
    readonly_cache = pinned or strict_cache or cache_only
    cache_decryption_failed = False
    cache_warning = None

    if cache.enabled and (not refresh or strict_cache):
        try:
            cached = cache.get(
                cache_entry,
                field,
                max_age_seconds=max_cache_age,
                legacy_entry=None if pinned else entry,
                legacy_field=attribute_name(field),
                **({"readonly": True} if readonly_cache else {}),
            )
        except CacheDecryptionUnavailable:
            cache_decryption_failed = True
            cache_warning = CACHE_DECRYPTION_WARNING
            cached = None
            if cache_only:
                return CredentialRead(
                    1, error="cache_decryption_unavailable; selected protected cache is unreadable",
                    source="cache",
                )
        except (OSError, RuntimeError, ValueError):
            if not pinned:
                raise
            return CredentialRead(
                1, error="cache_read_failed; selected protected cache could not be read",
                source="cache",
            )
        if cached is not None and not refresh:
            return CredentialRead(0, value=cached, source="cache")
    if cache_only:
        if pinned:
            code = "cache_missing" if cache.enabled else "cache_unavailable"
            error = f"{code}; selected entry is not available in the protected cache"
        else:
            error = f"{entry} [{field}] not in cache"
        return CredentialRead(1, error=error, source="cache")

    try:
        ready = cli.ensure_service()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
        if not strict_cache:
            raise
        return CredentialRead(
            1, error="service_unavailable; vault service could not be reached",
            warning=cache_warning,
        )
    if not ready:
        return CredentialRead(1, error="could not start vault service", warning=cache_warning)

    request = {
        "action": "get",
        "entry": entry,
        "field": field,
        "refresh": refresh or max_cache_age is not None,
    }
    if getattr(args, "prompt", False):
        request["allow_prompt"] = True
    authoritative = refresh or max_cache_age is not None
    read_lock = (
        cache.replacement_lock()
        if authoritative and not cache_decryption_failed else contextlib.nullcontext()
    )
    succeeded = False
    reconciliation_failed = False
    with read_lock:
        try:
            resp = cli.send_command(request, timeout=None)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
            if not strict_cache:
                raise
            return CredentialRead(
                1, error="service_read_failed; vault service request did not complete",
                warning=cache_warning,
            )
        if strict_cache and resp is not None and (
            not isinstance(resp, dict)
            or type(resp.get("ok")) is not bool
            or (resp["ok"] and not isinstance(resp.get("value"), str))
            or (
                resp.get("generation") is not None
                and type(resp["generation"]) is not int
            )
        ):
            return CredentialRead(
                1, error="service_response_invalid; vault service returned an invalid response",
                warning=cache_warning,
            )
        if resp and resp.get("ok"):
            value = resp["value"]
            succeeded = True
            if cache.enabled and not cache_decryption_failed:
                generation = resp.get("generation")
                if authoritative:
                    reconciliation_failed = not cache.reconcile_authoritative(
                        cache_entry,
                        field,
                        value,
                        int(generation) if generation is not None else None,
                        **({"strict": True, "legacy_entry": None if pinned else entry}
                           if strict_cache else {}),
                    )
                else:
                    cached_ok = cache.put(
                        cache_entry, field, value,
                        *(() if generation is None else (int(generation),)),
                        **({"strict": True, "legacy_entry": None if pinned else entry}
                           if strict_cache else {}),
                    )
                    reconciliation_failed = strict_cache and not cached_ok
    if reconciliation_failed or (succeeded and authoritative and cache_decryption_failed):
        return CredentialRead(
            2, error="credential was read, but the persistent cache could not be reconciled",
            warning=cache_warning,
        )
    if succeeded:
        return CredentialRead(
            0, value=value,
            warning=cache_warning,
        )

    error = resp.get("error", "unknown") if resp else "service unreachable"
    return CredentialRead(1, error=error, warning=cache_warning)


def cmd_get(args):
    result = read_credential(args)
    if result.warning:
        print(f"Warning: {result.warning}", file=sys.stderr)
    if result.code == 0:
        print(result.value)
    else:
        print(f"Error: {result.error}", file=sys.stderr)
    return result.code
