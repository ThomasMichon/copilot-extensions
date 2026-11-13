"""Context-scoped records inside the existing protected store, not legacy slots."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
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
    database = os.path.normcase(str(Path(kpdb).resolve())).encode()
    return PREFIX + hashlib.sha256(database).hexdigest() + ":"


def binding_for(kpdb: str, group: str, entry: str, field: str) -> str:
    installation = identity_cache_digest(
        kpdb, str(config.home_dir()), config.installation_id() or "",
    )
    subject = json.dumps(
        [installation, group, config.normalize_entry(entry, ""), canonical_field(field)],
        separators=(",", ":"),
    ).encode()
    return database_binding(kpdb) + hashlib.sha256(subject).hexdigest()


def _records(store: dict) -> dict:
    records = store.setdefault(COLLECTION, {})
    if not isinstance(records, dict):
        raise CacheProtectionError("Unsupported protected service cache schema")
    return records


def _available(persistent: PersistentCache) -> None:
    if not persistent.enabled:
        raise CacheProtectionError("Configured cache protection is unavailable")


def _counter(store: dict) -> int:
    value = store.get("service_generation", 0)
    if type(value) is not int or value < 0:
        raise CacheProtectionError("Invalid service-cache generation metadata")
    return value


def _entry(record: dict) -> str | None:
    value = record.get("entry")
    return config.normalize_entry(value, "") if isinstance(value, str) else None


def _field(record: dict) -> str | None:
    value = record.get("field")
    return canonical_field(value) if isinstance(value, str) else None


def _legacy_records(store: dict, entry: str, field: str) -> list[tuple[str, str, dict]]:
    result = []
    for path, fields in store["entries"].items():
        if config.normalize_entry(path, "") != entry or not isinstance(fields, dict):
            continue
        for attribute, record in fields.items():
            if canonical_field(attribute) == field and isinstance(record, dict):
                result.append((path, attribute, record))
    return result


class ServiceCacheStore:
    def __init__(self, persistent: PersistentCache, kpdb: str, group: str | None) -> None:
        self.persistent, self.kpdb = persistent, kpdb
        self.group = group if group is not None else config.load_config().group or ""
        self.database = database_binding(kpdb)

    def _identity(
        self, entry: str, field: str, canonical: bool = False,
    ) -> tuple[str, str, str]:
        entry = config.normalize_entry(entry, "" if canonical else self.group)
        field = canonical_field(field)
        return binding_for(self.kpdb, self.group, entry, field), entry, field

    def _discard(self, records: dict, entry: str, field: str | None = None) -> bool:
        changed = False
        for key, record in list(records.items()):
            if (
                isinstance(record, dict) and record.get("database") == self.database
                and _entry(record) == entry and (
                    field is None or _field(record) == field
                )
            ):
                del records[key]
                changed = True
        return changed

    def record(
        self, entry: str, field: str = "password", *, max_age_seconds: int | None = None,
        canonical: bool = False,
    ) -> dict | None:
        if not cache_enabled():
            return None
        _available(self.persistent)
        binding, entry, field = self._identity(entry, field, canonical)
        with service_cache_epochs.guard(self.persistent):
            store = self.persistent._read_store(readonly=True, strict=True)
            records = _records(store)
            epoch_state = service_cache_epochs.read(self.persistent._root_dir)
            _counter(store)
            legacy = _legacy_records(store, entry, field)
            if any("rotation" in record for _path, _attribute, record in legacy):
                # A later rollback must not resurrect a revoked service value.
                if self._discard(records, entry, field):
                    service_cache_epochs.revoke(self.persistent._root_dir, self.database, entry, field)
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
                    epoch_state, self.database, entry, field,
                )
                or max_age_seconds is not None and time.time() - stamp > max_age_seconds
            ):
                return None
            return dict(record)

    def put(
        self, entry: str, field: str, value: str, generation: int, *, operation: int | None = None,
        canonical: bool = False,
    ) -> int | None:
        if not cache_enabled():
            return None
        try:
            _available(self.persistent)
            binding, entry, field = self._identity(entry, field, canonical)
            with service_cache_epochs.guard(self.persistent):
                store = self.persistent._read_store(readonly=True, strict=True)
                records = _records(store)
                legacy = self.persistent._field_record(store, entry, field, strict=True)
                pending = legacy.get("rotation") if isinstance(legacy, dict) else None
                aliases = _legacy_records(store, entry, field)
                if operation is not None and (
                    not isinstance(pending, dict) or pending.get("operation") != operation
                    or pending.get("candidate") != value
                    or any(
                        "rotation" in record and (path != entry or attribute != field)
                        for path, attribute, record in aliases
                    )
                ):
                    return None
                if operation is None and any("rotation" in record for _p, _f, record in aliases):
                    if self._discard(records, entry, field):
                        service_cache_epochs.revoke(self.persistent._root_dir, self.database, entry, field)
                        self.persistent._write_store_unlocked(store)
                    return None
                prior = pending.get("previous") if isinstance(pending, dict) else legacy
                relevant = [
                    record for record in records.values()
                    if isinstance(record, dict) and record.get("database") == self.database
                    and _entry(record) == entry and _field(record) == field
                ]
                floors = [_counter(store), *(
                    record["generation"] for record in [*relevant, prior]
                    if isinstance(record, dict) and type(record.get("generation")) is int
                    and record["generation"] >= 0
                )]
                generation = max(generation, max(floors) + 1)
                if any(record.get("value") != value for record in relevant):
                    service_cache_epochs.revoke(self.persistent._root_dir, self.database, entry, field)
                epochs = service_cache_epochs.snapshot(
                    service_cache_epochs.read(self.persistent._root_dir), self.database, entry, field,
                )
                if operation is not None:
                    _, fields = self.persistent._fields_for_write(store, entry)
                    fields[field] = {
                        "value": value, "generation": generation, "cached_at_epoch": time.time(),
                        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                records[binding] = {
                    "binding": binding, "database": self.database, "entry": entry, "field": field,
                    "value": value, "generation": generation, "cached_at_epoch": time.time(),
                    "epochs": epochs,
                }
                store["service_generation"] = generation
                return generation if self.persistent._write_store_unlocked(store) else None
        except (OSError, RuntimeError, ValueError, ImportError):
            return None

    def invalidate(
        self, entry: str, field: str | None = None, *, legacy: bool = False,
        canonical: bool = False,
    ) -> bool:
        if not cache_enabled():
            return True
        try:
            entry = config.normalize_entry(entry, "" if canonical else self.group)
            field = canonical_field(field) if field is not None else None
            with service_cache_epochs.guard(self.persistent):
                service_cache_epochs.revoke(self.persistent._root_dir, self.database, entry, field)
                _available(self.persistent)
                store = self.persistent._read_store(readonly=True, strict=True)
                records = _records(store)
                changed = self._discard(records, entry, field)
                aliases = [
                    (path, fields) for path, fields in store["entries"].items()
                    if config.normalize_entry(path, "") == entry and isinstance(fields, dict)
                ]
                pending = any(
                    isinstance(record, dict) and "rotation" in record
                    for _path, fields in aliases for record in fields.values()
                )
                alternate_pending = any(
                    isinstance(record, dict) and "rotation" in record
                    and (path != entry or attribute != canonical_field(attribute))
                    for path, fields in aliases for attribute, record in fields.items()
                )
                if legacy and not pending:
                    for path, fields in aliases:
                        if field is None:
                            del store["entries"][path]
                        else:
                            for attribute in list(fields):
                                if canonical_field(attribute) == field:
                                    del fields[attribute]
                        changed = True
                saved = not changed or self.persistent._write_store_unlocked(store)
                return saved and not alternate_pending and not (legacy and pending)
        except (OSError, RuntimeError, ValueError, ImportError):
            return False


def revoke_records(persistent: PersistentCache, prefix: str) -> bool:
    if not cache_enabled():
        return True
    try:
        with service_cache_epochs.guard(persistent):
            service_cache_epochs.revoke(persistent._root_dir, None if prefix == PREFIX else prefix)
            _available(persistent)
            store = persistent._read_store(readonly=True, strict=True)
            records = _records(store)
            for key in list(records):
                if key.startswith(prefix):
                    del records[key]
            return persistent._write_store_unlocked(store)
    except (OSError, RuntimeError, ValueError, ImportError):
        return False
