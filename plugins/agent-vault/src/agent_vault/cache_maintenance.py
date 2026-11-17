"""Metadata-only maintenance of the selected service's protected records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import config, service_cache
from .cache import cache_enabled
from .credential_fields import canonical_field
from .service_cache_store import ServiceCacheStore

if TYPE_CHECKING:
    from .cache import PersistentCache
    from .service import VaultService

CONTRACT = "agent-vault.cache-maintenance"
VERSION = 1


def describe() -> dict[str, object]:
    return {
        "contract": CONTRACT,
        "version": VERSION,
        "operations": ["populate", "verify"],
        "owner": "selected-service",
        "returns_credentials": False,
    }


def _failure(code: str) -> dict[str, Any]:
    messages = {
        "invalid_request": "invalid protected-cache maintenance request",
        "cache_unavailable": "selected service protected cache is unavailable",
        "needs_unlock": "selected service requires an existing unlock for cache population",
        "entry_missing": "selected credential is unavailable",
        "cache_write_failed": "selected service could not verify its protected cache write",
    }
    return {
        "ok": False,
        "code": code,
        "error": messages[code],
        **({"needs_unlock": True} if code == "needs_unlock" else {}),
    }


def handle(
    vault: VaultService,
    persistent: PersistentCache,
    kpdb: str | None,
    group: str | None,
    request: dict[str, Any],
) -> dict[str, Any]:
    operation = request.get("operation")
    entry, field = request.get("entry"), request.get("field", "password")
    max_age = request.get("max_age_seconds")
    if (
        operation not in ("populate", "verify")
        or not isinstance(kpdb, str) or not kpdb
        or group is not None and not isinstance(group, str)
        or not isinstance(entry, str) or not entry.strip()
        or not isinstance(field, str) or not field.strip()
        or max_age is not None and (type(max_age) is not int or max_age < 0)
    ):
        return _failure("invalid_request")
    if not config.normalize_entry(entry, ""):
        return _failure("invalid_request")
    field = canonical_field(field)
    if not cache_enabled() or not persistent.enabled:
        return _failure("cache_unavailable")
    try:
        selected = ServiceCacheStore(persistent, kpdb, group)
        value: str | None = None
        generation = 0
        if operation == "populate":
            prior = getattr(vault._request_ctx, "require_existing_unlock", False)
            vault._request_ctx.require_existing_unlock = True
            try:
                value, generation = vault.get_with_generation(
                    kpdb, entry, field, selected.group, refresh=True,
                )
            finally:
                vault._request_ctx.require_existing_unlock = prior
            if value is None:
                return _failure("entry_missing" if vault.cli.has_password(kpdb) else "needs_unlock")
        service_cache.require_binding(vault, service_cache.binding_for(
            kpdb, selected.group, config.normalize_entry(entry, selected.group), field,
        ))
        record = selected.record(entry, field, max_age_seconds=max_age)
        if operation == "populate" and (
            record is None or record["value"] != value or record["generation"] != generation
        ):
            return _failure("cache_write_failed")
    except (OSError, RuntimeError, ValueError, ImportError):
        return _failure("cache_unavailable")
    return {
        "ok": True,
        "contract": CONTRACT,
        "version": VERSION,
        "present": record is not None,
        "owner": "selected-service",
    }
