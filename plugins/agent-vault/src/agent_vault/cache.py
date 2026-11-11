"""Persistent, encrypted on-disk credential cache for agent-vault.

The daemon caches unlocked credentials in memory with a TTL; that cache dies when
the daemon restarts and cannot serve reads while the vault is locked. The
*persistent* cache is a separate, **opt-in** tier: a Fernet-encrypted file on disk
that survives restarts and answers reads without unlocking the vault -- so a
consumer can fetch a previously-cached secret even when the master password is not
loaded (e.g. an unattended job on a locked box). On Windows without PyCryptodome,
new caches use native user-bound DPAPI directly, including on ARM64. Existing
Fernet caches use the same format through PyCryptodome; they are never discarded
to create a replacement. DPAPI caches remain readable if that package is installed.

It is inert unless enabled. Storing secrets on disk -- even encrypted with a
local key -- is a deliberate tradeoff, so the cache does nothing until switched on
via the ``AGENT_VAULT_CACHE`` env var (truthy) or a configured cache directory
(``AGENT_VAULT_CACHE_DIR``). When disabled, every operation is a safe no-op.
POSIX caches also require the optional ``pycryptodomex`` dependency.

Security posture: the Fernet key sits beside the cache file, **wrapped at rest** --
DPAPI (per-OS-user) on Windows via the ``kek`` module, or a ``0600`` file on POSIX.
On Windows the wrapped key is only unwrappable by the same user account; on POSIX it
is hygiene, not high security, so physical-access control and full-disk encryption
remain the real barriers. Never treat the on-disk cache as a trust boundary.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from .config import IS_WINDOWS, default_config_path, home_dir

# Enablement / location env vars.
CACHE_ENABLE_ENV = "AGENT_VAULT_CACHE"
CACHE_DIR_ENV = "AGENT_VAULT_CACHE_DIR"

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in _TRUTHY


def default_cache_dir() -> Path:
    """Return the default cache directory.

    Legacy mode keeps the cache alongside the global config file. Namespaced
    mode keeps it under the selected runtime root so two installation cells do
    not share persistent cached credentials.
    """
    if "AGENT_VAULT_HOME" in os.environ:
        return home_dir() / "cache"
    return default_config_path().parent / "cache"


def cache_enabled() -> bool:
    """Report whether the persistent cache is switched on for this process.

    Enabled when ``AGENT_VAULT_CACHE`` is truthy or ``AGENT_VAULT_CACHE_DIR`` is
    set. A configured directory implies intent, so it enables on its own.
    """
    return _truthy(os.environ.get(CACHE_ENABLE_ENV)) or bool(
        os.environ.get(CACHE_DIR_ENV)
    )


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


class PersistentCache:
    """Fernet-encrypted on-disk ``(entry, field) -> value`` cache.

    Disabled caches are inert. POSIX requires PyCryptodome; Windows can use
    native DPAPI for new caches without that package.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is not None:
            base = Path(base_dir)
        elif os.environ.get(CACHE_DIR_ENV):
            base = Path(os.environ[CACHE_DIR_ENV])
        else:
            base = default_cache_dir()
        self._root_dir = base
        self._base_dir = base
        self._fernet = None  # lazy
        self._available: bool | None = None
        self._recovery = None
        self._select_base(base)
        self._refresh_selection()

    def _select_base(self, base: Path) -> None:
        if self._base_dir != base:
            self._fernet = None
        self._base_dir = base
        self._cache_file = base / "credential-cache.enc"
        self._key_file = base / "credential-cache.key"
        self._lock_file = base / "credential-cache.lock"
        self._replacement_lock_file = base / "credential-replacement.lock"

    def _refresh_selection(self) -> None:
        if not cache_enabled():
            return
        from .cache_recovery import selected_cache

        base, recovery = selected_cache(self._root_dir)
        if self._recovery is not None and (
            recovery is None or recovery["source"] != self._recovery["source"]
        ):
            from .cache_dpapi import CacheProtectionError

            raise CacheProtectionError("selected Vault changed during cache recovery")
        self._recovery = recovery
        self._select_base(base)

    def prepare(self) -> dict:
        """Bound legacy adoption; only explicit population/setup calls this."""
        from .cache_recovery import prepare_cache

        result = prepare_cache(self._root_dir) if self.enabled else {"state": "disabled"}
        self._refresh_selection()
        return result

    # -- availability ------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when the cache is switched on *and* usable (crypto present)."""
        return cache_enabled() and self._crypto_available()

    def _crypto_available(self) -> bool:
        if IS_WINDOWS:
            return True
        if self._available is not None:
            return self._available
        try:
            from .protected_crypto import Fernet as _F  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def _get_fernet(self, *, readonly: bool = False):
        if self._fernet is not None and not readonly:
            return self._fernet
        if not self._crypto_available():
            return None
        from .cache_dpapi import MAGIC, CacheProtectionError, DpapiCacheCipher

        if IS_WINDOWS:
            if _path_exists(self._cache_file):
                with self._cache_file.open("rb") as stream:
                    native_cache = stream.read(len(MAGIC)) == MAGIC
                if native_cache:
                    self._fernet = DpapiCacheCipher()
                    return self._fernet
            try:
                from .protected_crypto import Fernet
            except ImportError as exc:
                if _path_exists(self._cache_file) or _path_exists(self._key_file):
                    raise CacheProtectionError(
                        "legacy cache requires pycryptodomex; existing cache was not replaced"
                    ) from exc
                self._fernet = DpapiCacheCipher()
                return self._fernet
        else:
            from .protected_crypto import Fernet

        from . import kek

        if not readonly:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        if _path_exists(self._key_file):
            blob = self._key_file.read_bytes()
            if kek.is_wrapped(blob):
                if readonly and not IS_WINDOWS and blob.startswith(kek.WRAP_MAGIC + b"D"):
                    raise CacheProtectionError("cache key requires an unavailable Windows wrapping backend")
                key = kek.unwrap_bytes(blob)
            else:
                key = blob.strip()
        else:
            if readonly:
                raise CacheProtectionError("existing cache key missing; cache was not replaced")
            key = Fernet.generate_key()
            self._write_key(key)
        self._fernet = Fernet(key)
        return self._fernet

    def _write_key(self, key: bytes) -> None:
        """Persist the Fernet key wrapped at rest (DPAPI per-user on Windows)."""
        from . import kek
        import tempfile

        wrapped = kek.wrap_bytes(key)
        fd, temporary = tempfile.mkstemp(dir=str(self._base_dir), prefix=".cache-key-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(wrapped)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._key_file)
            if not IS_WINDOWS:
                directory_fd = os.open(self._base_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temporary)

    # -- atomic file I/O ---------------------------------------------------

    def _read_store(self, *, readonly: bool = False, strict: bool = False) -> dict:
        self._refresh_selection()
        if not _path_exists(self._cache_file):
            if strict:
                from .cache_dpapi import CacheProtectionError, DpapiCacheCipher

                if _path_exists(self._key_file):
                    self._get_fernet(readonly=True)
                elif self._fernet is not None and not isinstance(self._fernet, DpapiCacheCipher):
                    raise CacheProtectionError("cache key disappeared; cache was not replaced")
            return {"v": 1, "entries": {}}
        if not _path_exists(self._key_file):
            from .cache_dpapi import MAGIC, CacheProtectionError

            with self._cache_file.open("rb") as stream:
                native = stream.read(len(MAGIC)) == MAGIC
            if not IS_WINDOWS or not native:
                raise CacheProtectionError("existing cache key missing; cache was not replaced")
        f = self._get_fernet(readonly=True)
        if f is None or not _path_exists(self._cache_file):
            return {"v": 1, "entries": {}}
        try:
            plaintext = f.decrypt(self._cache_file.read_bytes())
            data = json.loads(plaintext)
            if (
                not isinstance(data, dict) or data.get("v") != 1
                or not isinstance(data.get("entries"), dict)
            ):
                from .cache_dpapi import CacheProtectionError

                raise CacheProtectionError("unsupported protected cache schema; not replaced")
            return data
        except Exception as exc:
            from .cache_dpapi import CacheProtectionError

            if isinstance(exc, CacheProtectionError):
                raise
            raise CacheProtectionError("Protected cache data is invalid; not replaced") from exc

    @contextlib.contextmanager
    def _file_lock(self, lock_file: Path):
        self._base_dir.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "a+b") as lock_fd:
            deadline = time.monotonic() + 2.0
            locked = False
            try:
                while not locked:
                    try:
                        if IS_WINDOWS:
                            import msvcrt

                            lock_fd.seek(0)
                            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except (BlockingIOError, PermissionError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("protected cache is busy") from None
                        time.sleep(0.02)
                yield
            finally:
                with contextlib.suppress(Exception):
                    if locked and IS_WINDOWS:
                        lock_fd.seek(0)
                        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                    elif locked:
                        import fcntl

                        fcntl.flock(lock_fd, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def _store_lock(self):
        self._refresh_selection()
        with self._file_lock(self._lock_file):
            selected = self._base_dir
            self._refresh_selection()
            if selected != self._base_dir:
                raise RuntimeError("protected cache selection changed; retry the operation")
            yield

    @contextlib.contextmanager
    def replacement_lock(self):
        """Serialize a caller's complete credential replacement transaction."""
        if not self.enabled:
            yield
            return
        self._refresh_selection()
        with self._file_lock(self._replacement_lock_file):
            yield

    def _write_store_unlocked(self, store: dict) -> bool:
        f = self._get_fernet()
        if f is None:
            return False
        self._base_dir.mkdir(parents=True, exist_ok=True)

        import tempfile

        try:
            plaintext = json.dumps(store, separators=(",", ":")).encode()
            ciphertext = f.encrypt(plaintext)
            from . import kek
            from .cache_dpapi import DpapiCacheCipher

            if not isinstance(f, DpapiCacheCipher) and _path_exists(self._key_file):
                blob = self._key_file.read_bytes()
                if not kek.is_wrapped(blob):
                    with contextlib.suppress(OSError, RuntimeError, ValueError):
                        self._write_key(blob.strip())

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._base_dir), prefix=".cache-", suffix=".tmp"
            )
            try:
                os.write(fd, ciphertext)
                os.fsync(fd)
                os.close(fd)
                if not IS_WINDOWS:
                    os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, str(self._cache_file))
                if not IS_WINDOWS:
                    directory_fd = os.open(self._base_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(fd)
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
            return True
        except Exception:
            return False

    def _write_store(self, store: dict) -> bool:
        try:
            with self._store_lock():
                return self._write_store_unlocked(store)
        except Exception:
            return False

    # -- public API --------------------------------------------------------

    @staticmethod
    def _field_record(store: dict, entry: str, field: str, *, strict: bool = False):
        from .cache_dpapi import CacheProtectionError

        entries = store.get("entries")
        if not isinstance(entries, dict):
            return None
        fields = entries.get(entry)
        if strict and entry in entries and not isinstance(fields, dict):
            raise CacheProtectionError("Selected protected cache entry is invalid")
        if not isinstance(fields, dict):
            return None
        record = fields.get(field)
        if strict and field in fields:
            valid = isinstance(record, dict)
            if valid and "rotation" in record:
                rotation = record["rotation"]
                previous = rotation.get("previous") if isinstance(rotation, dict) else None
                valid = (
                    isinstance(rotation, dict)
                    and {"candidate", "operation", "previous"} <= rotation.keys()
                    and isinstance(rotation["candidate"], str)
                    and type(rotation["operation"]) is int
                    and (
                        previous is None
                        or (
                            isinstance(previous, dict) and "rotation" not in previous
                            and isinstance(previous.get("value"), str)
                        )
                    )
                )
            else:
                valid = valid and isinstance(record.get("value"), str)
            if not valid:
                raise CacheProtectionError("Selected protected cache record is invalid")
        return record

    @staticmethod
    def _fields_for_write(store: dict, entry: str) -> tuple[dict, dict]:
        entries = store.get("entries")
        if not isinstance(entries, dict):
            entries = {}
            store["entries"] = entries
        fields = entries.get(entry)
        if not isinstance(fields, dict):
            fields = {}
            entries[entry] = fields
        return entries, fields

    def get(
        self,
        entry: str,
        field: str = "password",
        max_age_seconds: int | None = None,
        *,
        legacy_entry: str | None = None,
        readonly: bool = False,
    ) -> str | None:
        """Read a cached value; readonly mode refuses migration and corrupt data."""
        if not self.enabled:
            return None
        self._refresh_selection()
        store = (
            self._read_store(readonly=True, strict=True)
            if readonly else self._read_store()
        )
        if not readonly and _path_exists(self._key_file):
            from . import kek

            if not kek.is_wrapped(self._key_file.read_bytes()):
                # Only a successfully decoded, still-locked store may migrate its key.
                with contextlib.suppress(TimeoutError):
                    with self._store_lock():
                        self._read_store(readonly=True, strict=True)
                        blob = self._key_file.read_bytes()
                        if not kek.is_wrapped(blob):
                            with contextlib.suppress(OSError, RuntimeError, ValueError):
                                self._write_key(blob.strip())
        rec = self._field_record(store, entry, field, strict=readonly)
        if rec is None and legacy_entry and legacy_entry != entry:
            rec = self._field_record(store, legacy_entry, field, strict=readonly)
            if rec is not None and not readonly:
                self.migrate_entry(legacy_entry, entry, field)
                migrated = self._field_record(self._read_store(), entry, field)
                if migrated is not None:
                    rec = migrated
        if not isinstance(rec, dict) or "rotation" in rec:
            return None
        if max_age_seconds is not None:
            cached_epoch = rec.get("cached_at_epoch")
            if not isinstance(cached_epoch, (int, float)):
                return None
            if time.time() - cached_epoch > max_age_seconds:
                return None
        return rec.get("value")

    def put(
        self,
        entry: str,
        field: str,
        value: str,
        generation: int = 0,
        *,
        strict: bool = False,
        legacy_entry: str | None = None,
    ) -> bool:
        """Cache a value (cache-through). No-op when disabled."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store(readonly=True, strict=True) if strict else self._read_store()
                if strict:
                    current = self._field_record(store, entry, field, strict=True)
                    if current is None and legacy_entry and legacy_entry != entry:
                        legacy = self._field_record(store, legacy_entry, field, strict=True)
                        if legacy is not None:
                            entry = legacy_entry
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                if isinstance(current, dict) and "rotation" in current:
                    return True
                if (
                    isinstance(current, dict)
                    and int(current.get("generation", 0)) > generation
                ):
                    return True
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def begin_replace(
        self,
        entry: str,
        field: str,
        value: str,
        operation: int,
    ) -> bool:
        """Journal an ambiguous credential replacement before its backend write."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                if isinstance(current, dict) and "rotation" in current:
                    return False
                fields[field] = {
                    "rotation": {
                        "candidate": value,
                        "operation": operation,
                        "previous": current,
                    },
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def pending_replace(self, entry: str, field: str) -> dict | None:
        """Return a pending replacement journal, if one exists."""
        if not self.enabled:
            return None
        rec = self._field_record(self._read_store(), entry, field)
        if not isinstance(rec, dict):
            return None
        rotation = rec.get("rotation")
        return rotation if isinstance(rotation, dict) else None

    def rollback_replace(
        self,
        entry: str,
        field: str,
        operation: int,
    ) -> bool:
        """Restore the prior value when a journaled backend replacement fails."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                entries = store.get("entries")
                current = self._field_record(store, entry, field)
                rotation = current.get("rotation") if isinstance(current, dict) else None
                if (
                    not isinstance(entries, dict)
                    or
                    not isinstance(rotation, dict)
                    or int(rotation.get("operation", -1)) != operation
                ):
                    return False
                previous = rotation.get("previous")
                if previous is None:
                    del entries[entry][field]
                    if not entries[entry]:
                        del entries[entry]
                else:
                    entries[entry][field] = previous
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def complete_replace(
        self,
        entry: str,
        field: str,
        value: str,
        operation: int,
        generation: int,
    ) -> bool:
        """Finalize one owned journal without overwriting a newer value."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                rotation = current.get("rotation") if isinstance(current, dict) else None
                if isinstance(rotation, dict):
                    if int(rotation.get("operation", -1)) != operation:
                        return False
                elif (
                    isinstance(current, dict)
                    and int(current.get("generation", 0)) > generation
                ):
                    return True
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def replace(
        self,
        entry: str,
        field: str,
        value: str,
        generation: int = 0,
    ) -> bool:
        """Authoritatively replace a cached value and clear any journal."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                _, fields = self._fields_for_write(store, entry)
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def reconcile_authoritative(
        self,
        entry: str,
        field: str,
        value: str,
        generation: int | None = None,
        *,
        strict: bool = False,
        legacy_entry: str | None = None,
    ) -> bool:
        """Apply a confirmed live value, clearing a pending replacement journal.

        Unlike :meth:`put`, this path may replace a positive-generation record
        when the authoritative source does not expose generations. A
        generation-aware response still cannot overwrite a newer normal record.
        """
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store(readonly=True, strict=True) if strict else self._read_store()
                if strict:
                    current = self._field_record(store, entry, field, strict=True)
                    if current is None and legacy_entry and legacy_entry != entry:
                        legacy = self._field_record(store, legacy_entry, field, strict=True)
                        if legacy is not None:
                            entry = legacy_entry
                _, fields = self._fields_for_write(store, entry)
                current = fields.get(field)
                if (
                    generation is not None
                    and isinstance(current, dict)
                    and "rotation" not in current
                    and int(current.get("generation", 0)) > generation
                ):
                    return True
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation if generation is not None else 0,
                }
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def recover_missing(
        self, entry: str, field: str, value: str, generation: int | None = None,
    ) -> bool:
        """Insert a live recovery value only when no current record owns the field."""
        if (
            not self.enabled or not isinstance(value, str)
            or generation is not None and (type(generation) is not int or generation < 0)
        ):
            return False
        from .cache_recovery import require_source

        try:
            record = self._recovery
            require_source(self._root_dir, record)
            with self._store_lock():
                require_source(self._root_dir, record)
                store = self._read_store(readonly=True, strict=True)
                entries = store["entries"]
                if entry in entries:
                    fields = entries[entry]
                    if not isinstance(fields, dict):
                        return False
                    if field in fields:
                        existing = self._field_record(store, entry, field, strict=True)
                        return "rotation" not in existing
                _, fields = self._fields_for_write(store, entry)
                fields[field] = {
                    "value": value,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "cached_at_epoch": time.time(),
                    "generation": generation if generation is not None else 0,
                }
                require_source(self._root_dir, record)
                return self._write_store_unlocked(store)
        except (OSError, RuntimeError, ValueError):
            return False

    def migrate_entry(
        self,
        source_entry: str,
        target_entry: str,
        field: str,
    ) -> bool:
        """Move one legacy cache record when its normalized key is absent."""
        if not self.enabled or source_entry == target_entry:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                if self._field_record(store, target_entry, field) is not None:
                    return True
                entries = store.get("entries")
                if not isinstance(entries, dict):
                    return False
                source_fields = entries.get(source_entry)
                if not isinstance(source_fields, dict) or field not in source_fields:
                    return False
                record = source_fields[field]
                _, target_fields = self._fields_for_write(store, target_entry)
                target_fields[field] = record
                del source_fields[field]
                if not source_fields:
                    del entries[source_entry]
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def invalidate(self, entry: str, field: str | None = None) -> bool:
        """Drop a cached entry (or one field). No-op when disabled."""
        if not self.enabled:
            return False
        try:
            with self._store_lock():
                store = self._read_store()
                entries = store.get("entries")
                if not isinstance(entries, dict) or entry not in entries:
                    return False
                fields = entries[entry]
                if not isinstance(fields, dict):
                    del entries[entry]
                    return self._write_store_unlocked(store)
                if field is None:
                    del entries[entry]
                elif field in fields:
                    del fields[field]
                    if not fields:
                        del entries[entry]
                else:
                    return False
                return self._write_store_unlocked(store)
        except Exception:
            return False

    def clear(self) -> bool:
        """Wipe the entire cache file. Safe when the file is absent."""
        try:
            self._refresh_selection()
            if self._cache_file.exists():
                self._cache_file.unlink()
            return True
        except OSError:
            return False

    def status(self) -> dict:
        """Return cache metadata (enabled, location, counts, staleness)."""
        self._refresh_selection()
        readable = True
        try:
            store = self._read_store(readonly=True, strict=True)
        except (OSError, RuntimeError, ValueError, ImportError):
            readable = False
            store = {"entries": {}}
        entries = store.get("entries", {})
        if not isinstance(entries, dict):
            entries = {}
        total_fields = sum(
            len(fields)
            for fields in entries.values()
            if isinstance(fields, dict)
        )
        oldest = newest = None
        for fields in entries.values():
            if not isinstance(fields, dict):
                continue
            for rec in fields.values():
                if not isinstance(rec, dict):
                    continue
                ts = rec.get("cached_at", "")
                if ts:
                    if oldest is None or ts < oldest:
                        oldest = ts
                    if newest is None or ts > newest:
                        newest = ts
        return {
            "enabled": cache_enabled(),
            "available": self._crypto_available(),
            "cache_dir": str(self._base_dir),
            "cache_file": str(self._cache_file),
            "cache_exists": self._cache_file.exists(),
            "key_exists": self._key_file.exists(),
            "entry_count": len(entries),
            "field_count": total_fields,
            "oldest": oldest,
            "newest": newest,
            "readable": readable,
            "recovery": self._recovery,
        }


def get_cache(base_dir: str | Path | None = None) -> PersistentCache:
    """Return a :class:`PersistentCache` bound to the current environment."""
    return PersistentCache(base_dir)
