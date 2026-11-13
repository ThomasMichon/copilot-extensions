"""Service-owned access to explicitly attributed protected credential records."""

from __future__ import annotations

import hashlib
import math
import time
from typing import TYPE_CHECKING

from . import config
from .cache import PersistentCache
from .identity_context import identity_cache_digest

if TYPE_CHECKING:
    from .service import VaultService

PREFIX = "agent-vault.service-cache.v1:"
UNAVAILABLE = "service_cache_unavailable; selected protected cache could not be used"


class ServiceCacheUnavailable(RuntimeError):
    pass


def database_binding(kpdb: str) -> str:
    identity = identity_cache_digest(
        kpdb, str(config.home_dir()), config.installation_id() or "",
    )
    return PREFIX + identity + ":"


def binding_for(kpdb: str, group: str | None) -> str:
    effective_group = group if group is not None else config.load_config().group
    group_key = hashlib.sha256((effective_group or "").encode("utf-8")).hexdigest()
    return database_binding(kpdb) + group_key


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
        key = (kpdb, entry, field)
        binding = binding_for(kpdb, group) if kpdb and persistent.enabled else None
        service._request_ctx.cache_warning = None
        if binding is not None and any(
            binding.startswith(prefix) for prefix in service._blocked_service_cache
        ):
            raise ServiceCacheUnavailable(UNAVAILABLE)
        if refresh and binding is not None and not persistent.invalidate(
            entry, field, missing_ok=True, preserve_pending=True,
        ):
            raise ServiceCacheUnavailable(UNAVAILABLE)
        if not refresh:
            if binding is not None:
                try:
                    record = persistent.get_record(entry, field)
                except (OSError, RuntimeError, ValueError, ImportError) as exc:
                    raise ServiceCacheUnavailable(UNAVAILABLE) from exc
                if record is not None and record.get("service_binding") == binding:
                    generation = record.get("generation")
                    cached_at = record.get("cached_at_epoch")
                    if (
                        isinstance(record.get("value"), str)
                        and type(generation) is int and generation >= 0
                        and type(cached_at) in (int, float) and math.isfinite(cached_at)
                        and 0 <= cached_at <= time.time()
                    ):
                        service.cache[key] = record["value"]
                        service._credential_generations[key] = generation
                        return record["value"]
            elif key in service.cache:
                return service.cache[key]

        if not service.ensure_unlocked(kpdb):
            return None
        value = service.cli.get_entry(kpdb, entry, field)
        if value is not None:
            generation = time.time_ns()
            service.cache[key] = value
            service._credential_generations[key] = generation
            if binding is not None and not persistent.put(
                entry, field, value, generation, strict=True, service_binding=binding,
            ):
                service._request_ctx.cache_warning = "service_cache_write_failed"
        return value


def invalidate_entry(persistent: PersistentCache, entry: str) -> bool:
    return not persistent.enabled or persistent.invalidate(
        entry, missing_ok=True, preserve_pending=True,
    )


def revoke(service: VaultService, persistent: PersistentCache, kpdb: str | None) -> bool:
    prefix = database_binding(kpdb) if kpdb else PREFIX
    service._blocked_service_cache.add(prefix)
    if persistent.enabled and not persistent.invalidate_service_bindings(prefix):
        return False
    if kpdb:
        service._blocked_service_cache.discard(prefix)
    else:
        service._blocked_service_cache.clear()
    return True
