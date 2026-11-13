"""Service-owned access to explicitly attributed protected credential records."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
import time
from typing import TYPE_CHECKING

from . import config
from .cache import PersistentCache, cache_enabled, get_cache as _raw_get_cache
from .credential_fields import canonical_field
from .service_cache_store import (
    PREFIX as PREFIX, RevocationChanged, ServiceCacheStore, binding_for as binding_for,
    database_binding as database_binding, revoke_records,
)
from . import service_cache_epochs

if TYPE_CHECKING:
    from .service import VaultService

UNAVAILABLE = "service_cache_unavailable; selected protected cache could not be used"


class ServiceCacheUnavailable(RuntimeError):
    pass


def get_cache() -> PersistentCache:
    try:
        return _raw_get_cache()
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        raise ServiceCacheUnavailable(UNAVAILABLE) from exc


@contextlib.contextmanager
def backend_custody(persistent: PersistentCache, kpdb: str | None) -> Iterator[None]:
    with contextlib.ExitStack() as stack:
        if cache_enabled() and kpdb:
            try:
                stack.enter_context(service_cache_epochs.backend_guard(persistent, database_binding(kpdb)))
            except (OSError, RuntimeError, ValueError, ImportError) as exc:
                raise ServiceCacheUnavailable(UNAVAILABLE) from exc
        yield


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
        binding = binding_for(kpdb, selected.group, entry, field) if selected is not None else ""
        if selected is not None and any(
            binding.startswith(prefix) and (prefix != binding or not refresh)
            for prefix in service._blocked_service_cache
        ):
            raise ServiceCacheUnavailable(UNAVAILABLE)
        if selected is not None:
            try:
                # Even refresh observes pending revocation, but never deletes a
                # caller's current legacy record or replacement journal.
                record = selected.record(entry, field, canonical=True)
            except (OSError, RuntimeError, ValueError, ImportError) as exc:
                raise ServiceCacheUnavailable(UNAVAILABLE) from exc
            if not refresh and record is not None:
                service.cache[key] = record["value"]
                service._credential_generations[key] = record["generation"]
                return record["value"]
        elif not refresh:
            if key in service.cache:
                return service.cache[key]

        with backend_custody(persistent, kpdb):
            service._check_password_ttl()
            if selected is not None:
                try:
                    record = selected.record(entry, field, canonical=True)
                except (OSError, RuntimeError, ValueError, ImportError) as exc:
                    raise ServiceCacheUnavailable(UNAVAILABLE) from exc
                if not refresh and record is not None:
                    service.cache[key] = record["value"]
                    service._credential_generations[key] = record["generation"]
                    return record["value"]
            if not service.ensure_unlocked(kpdb):
                return None
            try:
                revocation = (
                    selected.revocation_state(entry, field, canonical=True)
                    if selected is not None else None
                )
            except (OSError, RuntimeError, ValueError, ImportError) as exc:
                raise ServiceCacheUnavailable(UNAVAILABLE) from exc
            value = service.cli.get_entry(kpdb, entry, field)
            if value is not None:
                generation = max(time.time_ns(), service._credential_generations.get(key, 0) + 1)
                if selected is not None:
                    try:
                        written = selected.put(
                            entry, field, value, generation, canonical=True,
                            expected_revocation=revocation,
                        )
                    except RevocationChanged as exc:
                        raise ServiceCacheUnavailable(UNAVAILABLE) from exc
                    if type(written) is int:
                        generation = written
                        service._blocked_service_cache.discard(binding)
                    else:
                        service._request_ctx.cache_warning = "service_cache_write_failed"
                        if record is not None and record["value"] != value:
                            service._blocked_service_cache.add(binding)
                service.cache[key] = value
                service._credential_generations[key] = generation
            return value


def invalidate_entry(
    persistent: PersistentCache, kpdb: str, entry: str, group: str | None,
    field: str | None = None, *, legacy: bool = True,
) -> bool:
    if not cache_enabled():
        return True
    return ServiceCacheStore(persistent, kpdb, group).invalidate(
        entry, field, legacy=legacy, canonical=True,
    )


def remember_password(
    service: VaultService, persistent: PersistentCache, kpdb: str, entry: str,
    password: str, generation: int, operation: int | None,
) -> int | None:
    if not cache_enabled():
        return generation
    return ServiceCacheStore(
        persistent, kpdb, getattr(service._request_ctx, "group", None),
    ).put(entry, "password", password, generation, operation=operation, canonical=True)


def update_password_cache(
    service: VaultService, kpdb: str, entry: str, password: str, persistent: PersistentCache,
    generation: int, operation: int | None,
) -> int:
    committed = remember_password(service, persistent, kpdb, entry, password, generation, operation)
    if type(committed) is not int or committed < 0:
        raise RuntimeError("Password updated, but the offline cache update failed")
    clear_memory(service, kpdb, entry)
    cache_key = kpdb, entry, "password"
    service.cache[cache_key] = password
    service._credential_generations[cache_key] = committed
    return committed


def clear_memory(service: VaultService, kpdb: str, *entries: str) -> int:
    targets = {config.normalize_entry(entry, "") for entry in entries}
    keys = [
        key for key in service.cache
        if key[0] == kpdb and config.normalize_entry(key[1], "") in targets
    ]
    for key in keys:
        del service.cache[key]
        service._credential_generations.pop(key, None)
    return len(keys)


def revoke(service: VaultService, persistent: PersistentCache, kpdb: str | None) -> bool:
    prefix = database_binding(kpdb) if kpdb else PREFIX
    service._blocked_service_cache.add(prefix)
    if not revoke_records(persistent, prefix):
        return False
    if kpdb:
        service._blocked_service_cache.difference_update(
            blocked for blocked in list(service._blocked_service_cache) if blocked.startswith(prefix)
        )
    else:
        service._blocked_service_cache.clear()
    return True
