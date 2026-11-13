"""Context-scoped records inside the existing protected store, not legacy slots."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import TYPE_CHECKING

from . import config, service_cache_epochs
from .cache import cache_enabled
from .cache_dpapi import CacheProtectionError
from .credential_fields import canonical_field
from .identity_context import identity_cache_digest

if TYPE_CHECKING:
    from .cache import PersistentCache

PREFIX = "agent-vault.service-cache.v1:"
COLLECTION = "service_entries"


def database_binding(kpdb: str) -> str:
    return PREFIX + identity_cache_digest(
        kpdb, str(config.home_dir()), config.installation_id() or "",
    ) + ":"


def binding_for(kpdb: str, group: str, entry: str, field: str) -> str:
    subject = json.dumps([group, entry, canonical_field(field)], separators=(",", ":")).encode()
    return database_binding(kpdb) + hashlib.sha256(subject).hexdigest()


def _records(store: dict) -> dict:
    records = store.setdefault(COLLECTION, {})
    if not isinstance(records, dict):
        raise CacheProtectionError("Unsupported protected service cache schema")
    return records


def _available(persistent: PersistentCache) -> None:
    if not persistent.enabled:
        raise CacheProtectionError("Configured cache protection is unavailable")


class ServiceCacheStore:
    def __init__(self, persistent: PersistentCache, kpdb: str, group: str | None) -> None:
        self.persistent, self.kpdb = persistent, kpdb
        self.group = group if group is not None else config.load_config().group or ""
        self.database = database_binding(kpdb)

    def _identity(self, entry: str, field: str) -> tuple[str, str, str]:
        entry = config.normalize_entry(entry, self.group)
        field = canonical_field(field)
        return binding_for(self.kpdb, self.group, entry, field), entry, field

    def _discard(self, records: dict, entry: str, field: str | None = None) -> bool:
        changed = False
        for key, record in list(records.items()):
            if (
                isinstance(record, dict) and record.get("database") == self.database
                and record.get("entry") == entry and (field is None or record.get("field") == field)
            ):
                del records[key]
                changed = True
        return changed

    def record(
        self, entry: str, field: str = "password", *, max_age_seconds: int | None = None,
    ) -> dict | None:
        if not cache_enabled():
            return None
        _available(self.persistent)
        binding, entry, field = self._identity(entry, field)
        with self.persistent._store_lock():
            store = self.persistent._read_store(readonly=True, strict=True)
            records = _records(store)
            legacy = self.persistent._field_record(store, entry, field, strict=True)
            if isinstance(legacy, dict) and "rotation" in legacy:
                # A later rollback must not resurrect a revoked service value.
                if self._discard(records, entry, field):
                    service_cache_epochs.revoke(self.persistent._base_dir, self.database, entry, field)
                    if not self.persistent._write_store_unlocked(store):
                        raise CacheProtectionError("Protected service cache revocation failed")
                return None
            record = records.get(binding)
            if not isinstance(record, dict):
                return None
            stamp, generation = record.get("cached_at_epoch"), record.get("generation")
            if (
                record.get("binding") != binding or record.get("database") != self.database
                or record.get("entry") != entry or record.get("field") != field
                or not isinstance(record.get("value"), str)
                or type(generation) is not int or generation < 0
                or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= stamp <= time.time()
                or record.get("epochs") != service_cache_epochs.snapshot(
                    service_cache_epochs.read(self.persistent._base_dir), self.database, entry, field,
                )
                or max_age_seconds is not None and time.time() - stamp > max_age_seconds
            ):
                return None
            return dict(record)

    def put(self, entry: str, field: str, value: str, generation: int) -> bool:
        if not cache_enabled():
            return False
        try:
            _available(self.persistent)
            binding, entry, field = self._identity(entry, field)
            with self.persistent._store_lock():
                store = self.persistent._read_store(readonly=True, strict=True)
                records = _records(store)
                legacy = self.persistent._field_record(store, entry, field, strict=True)
                if isinstance(legacy, dict) and "rotation" in legacy:
                    if self._discard(records, entry, field):
                        service_cache_epochs.revoke(self.persistent._base_dir, self.database, entry, field)
                        self.persistent._write_store_unlocked(store)
                    return False
                epochs = service_cache_epochs.snapshot(
                    service_cache_epochs.read(self.persistent._base_dir), self.database, entry, field,
                )
                current = records.get(binding)
                if (
                    isinstance(current, dict) and current.get("epochs") == epochs
                    and type(current.get("generation")) is int
                ):
                    if current["generation"] > generation:
                        return False
                    if current["generation"] == generation and current.get("value") == value:
                        return True
                records[binding] = {
                    "binding": binding, "database": self.database, "entry": entry, "field": field,
                    "value": value, "generation": generation, "cached_at_epoch": time.time(),
                    "epochs": epochs,
                }
                return self.persistent._write_store_unlocked(store)
        except (OSError, RuntimeError, ValueError, ImportError):
            return False

    def invalidate(
        self, entry: str, field: str | None = None, *, legacy: bool = False,
    ) -> bool:
        if not cache_enabled():
            return True
        try:
            entry = config.normalize_entry(entry, self.group)
            field = canonical_field(field) if field is not None else None
            with self.persistent._store_lock():
                service_cache_epochs.revoke(self.persistent._base_dir, self.database, entry, field)
                _available(self.persistent)
                store = self.persistent._read_store(readonly=True, strict=True)
                records = _records(store)
                changed = self._discard(records, entry, field)
                fields = store["entries"].get(entry)
                pending = isinstance(fields, dict) and any(
                    isinstance(record, dict) and "rotation" in record
                    for record in fields.values()
                )
                if legacy and not pending and entry in store["entries"]:
                    if field is None:
                        del store["entries"][entry]
                    elif isinstance(fields, dict):
                        fields.pop(field, None)
                    changed = True
                saved = not changed or self.persistent._write_store_unlocked(store)
                return saved and not (legacy and pending)
        except (OSError, RuntimeError, ValueError, ImportError):
            return False


def revoke_records(persistent: PersistentCache, prefix: str) -> bool:
    if not cache_enabled():
        return True
    try:
        with persistent._store_lock():
            service_cache_epochs.revoke(persistent._base_dir, None if prefix == PREFIX else prefix)
            _available(persistent)
            store = persistent._read_store(readonly=True, strict=True)
            records = _records(store)
            for key in list(records):
                if key.startswith(prefix):
                    del records[key]
            return persistent._write_store_unlocked(store)
    except (OSError, RuntimeError, ValueError, ImportError):
        return False
