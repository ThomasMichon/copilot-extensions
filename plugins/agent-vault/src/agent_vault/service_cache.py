"""Service-owned access to explicitly attributed protected credential records."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from . import config
from .cache import PersistentCache, cache_enabled
from .credential_fields import canonical_field
from .service_cache_store import (
    PREFIX as PREFIX, ServiceCacheStore, binding_for as binding_for,
    database_binding as database_binding, revoke_records,
)

if TYPE_CHECKING:
    from .service import VaultService

UNAVAILABLE = "service_cache_unavailable; selected protected cache could not be used"


class ServiceCacheUnavailable(RuntimeError):
    pass


def read(
    service: VaultService,
    persistent: PersistentCache,
    kpdb: str,
    entry: str,
    field: str,
    group: str | None,
    refresh: bool,
) -> str | None:
    with service._credential_lock:
        entry = config.normalize_entry(entry, group)
        field = canonical_field(field)
        key = (kpdb, entry, field)
        selected = ServiceCacheStore(persistent, kpdb, group) if kpdb and cache_enabled() else None
        service._request_ctx.cache_warning = None
        if selected is not None and any(
            selected.database.startswith(prefix) for prefix in service._blocked_service_cache
        ):
            raise ServiceCacheUnavailable(UNAVAILABLE)
        if selected is not None:
            try:
                # Even refresh observes pending revocation, but never deletes a
                # caller's current legacy record or replacement journal.
                record = selected.record(entry, field)
            except (OSError, RuntimeError, ValueError, ImportError) as exc:
                raise ServiceCacheUnavailable(UNAVAILABLE) from exc
            if not refresh and record is not None:
                service.cache[key] = record["value"]
                service._credential_generations[key] = record["generation"]
                return record["value"]
        elif not refresh:
            if key in service.cache:
                return service.cache[key]

        if not service.ensure_unlocked(kpdb):
            return None
        value = service.cli.get_entry(kpdb, entry, field)
        if value is not None:
            generation = time.time_ns()
            service.cache[key] = value
            service._credential_generations[key] = generation
            if selected is not None and not selected.put(entry, field, value, generation):
                service._request_ctx.cache_warning = "service_cache_write_failed"
        return value


def invalidate_entry(
    persistent: PersistentCache, kpdb: str, entry: str, group: str | None,
    field: str | None = None, *, legacy: bool = True,
) -> bool:
    if not cache_enabled():
        return True
    return ServiceCacheStore(persistent, kpdb, group).invalidate(entry, field, legacy=legacy)


def remember_password(
    service: VaultService, persistent: PersistentCache, kpdb: str, entry: str,
    password: str, generation: int,
) -> bool:
    if not cache_enabled():
        return True
    return ServiceCacheStore(
        persistent, kpdb, getattr(service._request_ctx, "group", None),
    ).put(entry, "password", password, generation)


def revoke(service: VaultService, persistent: PersistentCache, kpdb: str | None) -> bool:
    prefix = database_binding(kpdb) if kpdb else PREFIX
    service._blocked_service_cache.add(prefix)
    if not revoke_records(persistent, prefix):
        return False
    if kpdb:
        service._blocked_service_cache.discard(prefix)
    else:
        service._blocked_service_cache.clear()
    return True
